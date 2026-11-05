"""Offline integration coverage for atomic parent/chunk ingestion."""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tenantrag import AuthError, FakeEmbedder, QuotaError, TenantRAG


class RecordingEmbedder(FakeEmbedder):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail_on = None
        self.hook = None

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.hook is not None:
            hook, self.hook = self.hook, None
            hook()
        if len(self.calls) == self.fail_on:
            raise RuntimeError("injected provider failure")
        return super().embed(texts)


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "rag.db")
        self.embedder = RecordingEmbedder()
        self.rag = TenantRAG(self.path, embedder=self.embedder)
        self.addCleanup(self.rag.close)
        self.rag.register("a", "tenant-a-key", quota_docs=2)
        self.rag.register("b", "tenant-b-key", quota_docs=2)
        self.key = "tenant-a-key"

    def snapshot(self):
        return {table: self.rag.store._con.execute(
            "SELECT * FROM " + table + " ORDER BY rowid").fetchall()
            for table in ("docs", "docs_fts", "parent_docs", "doc_chunks", "embedding_config")}

    def chunks(self, tenant="a"):
        return self.rag.store._con.execute(
            "SELECT d.doc_id,d.text,d.metadata,c.start,c.end FROM docs d "
            "JOIN doc_chunks c USING(tenant_id,doc_id) WHERE tenant_id=? ORDER BY c.start",
            (tenant,)).fetchall()

    def assert_clean(self):
        con = self.rag.store._con
        self.assertFalse(con.in_transaction)
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        docs = con.execute("SELECT tenant_id,doc_id,text FROM docs ORDER BY tenant_id,doc_id").fetchall()
        fts = con.execute("SELECT tenant_id,doc_id,text FROM docs_fts ORDER BY tenant_id,doc_id").fetchall()
        self.assertEqual(docs, fts)

    def test_unicode_offsets_metadata_and_parent_listing(self):
        text = "# 标题🌏\r\n说明 e\u0301。\n\n" * 12
        result = self.rag.ingest(self.key, text, "parent", {"label": "中文"},
                                 source="manual.md", format="markdown", max_chars=25, overlap=5)
        chunks = self.chunks()
        self.assertEqual(result["chunks"], len(chunks))
        self.assertGreater(len(chunks), 1)
        for index, (_, chunk, metadata, start, end) in enumerate(chunks):
            self.assertEqual(chunk, text[start:end])
            meta = json.loads(metadata)
            self.assertEqual(meta["label"], "中文")
            self.assertEqual(meta["_chunk"], {"parent_id": "parent", "source": "manual.md",
                                             "start": start, "end": end, "index": index})
        self.assertEqual(self.rag.list_docs(self.key),
                         [{"doc_id": "parent", "text": text, "metadata": {"label": "中文"}}])
        hits = self.rag.search(self.key, "说明", k=100)
        self.assertTrue(hits)
        self.assertTrue(all(hit["metadata"]["_chunk"]["parent_id"] == "parent" for hit in hits))
        self.assertEqual(self.rag.usage(self.key)["docs"], 1)
        self.assert_clean()

    def test_idempotent_retry_skips_provider_and_all_writes(self):
        kwargs = dict(doc_id="same", metadata={"b": 2, "a": 1}, source="s", max_chars=8, overlap=2)
        first = self.rag.ingest(self.key, "text goes here and there", **kwargs)
        before, calls = self.snapshot(), len(self.embedder.calls)
        kwargs["metadata"] = {"a": 1, "b": 2}
        again = self.rag.ingest(self.key, "text goes here and there", **kwargs)
        self.assertFalse(first["unchanged"])
        self.assertTrue(again["unchanged"])
        self.assertEqual(first["content_hash"], again["content_hash"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.embedder.calls), calls)

    def test_metadata_and_source_update_reuse_vectors(self):
        first = self.rag.ingest(self.key, "reusable chunk text", "same", {"v": 1}, source="old")
        calls = len(self.embedder.calls)
        second = self.rag.ingest(self.key, "reusable chunk text", "same", {"v": 2}, source="new")
        self.assertFalse(second["unchanged"])
        self.assertNotEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(len(self.embedder.calls), calls)
        metadata = json.loads(self.chunks()[0][2])
        self.assertEqual(metadata["v"], 2)
        self.assertEqual(metadata["_chunk"]["source"], "new")
        self.assert_clean()

    def test_duplicate_text_across_batch_embedded_once(self):
        result = self.rag.ingest_batch(self.key, [
            {"text": "abcdefghabcdefgh", "doc_id": "one"},
            {"text": "abcdefghabcdefgh", "doc_id": "two"}], max_chars=8, overlap=0, batch_size=1)
        self.assertEqual([r["chunks"] for r in result], [2, 2])
        self.assertEqual(self.embedder.calls, [["abcdefgh"]])
        self.assertEqual(self.rag.usage(self.key)["docs"], 2)
        self.assert_clean()

    def test_incremental_update_embeds_only_changed_chunk(self):
        self.rag.ingest(self.key, "abcdefgh", "parent", max_chars=4, overlap=0)
        self.embedder.calls.clear()
        self.rag.ingest(self.key, "abcdWXYZ", "parent", max_chars=4, overlap=0)
        self.assertEqual(self.embedder.calls, [["WXYZ"]])
        self.assertEqual([r[1] for r in self.chunks()], ["abcd", "WXYZ"])
        self.assert_clean()

    def test_reopen_preserves_parent_retry_and_delete(self):
        first = self.rag.ingest(self.key, "persistent content " * 8, "parent",
                                source="persisted.md", max_chars=20, overlap=2)
        with TenantRAG(self.path, embedder=RecordingEmbedder()) as reopened:
            again = reopened.ingest(self.key, "persistent content " * 8, "parent",
                                     source="persisted.md", max_chars=20, overlap=2)
            self.assertTrue(again["unchanged"])
            self.assertEqual(first["content_hash"], again["content_hash"])
            self.assertEqual(reopened.embedder.calls, [])
            self.assertEqual(reopened.usage(self.key)["docs"], 1)
            self.assertTrue(reopened.delete(self.key, "parent"))
            self.assertEqual(reopened.list_docs(self.key), [])
        self.assertEqual(self.chunks(), [])
        self.assert_clean()

    def test_reuse_existing_standalone_text_and_tenant_cache_isolation(self):
        self.rag.index(self.key, "existing text", "standalone")
        calls = len(self.embedder.calls)
        self.rag.ingest(self.key, "existing text", "parent")
        self.assertEqual(len(self.embedder.calls), calls)
        self.rag.ingest("tenant-b-key", "existing text", "parent")
        self.assertEqual(len(self.embedder.calls), calls + 1)
        self.assert_clean()

    def test_duplicate_parent_ids_rejected_before_embedding(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.rag.ingest_batch(self.key, [{"text": "one", "doc_id": "p"},
                                            {"text": "two", "doc_id": "p"}])
        self.assertEqual(self.embedder.calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_quota_counts_parents_and_batch_failure_is_atomic(self):
        self.rag.ingest(self.key, "very long text " * 20, "one", max_chars=15, overlap=0)
        self.assertEqual(self.rag.usage(self.key)["docs"], 1)
        before = self.snapshot()
        with self.assertRaises(QuotaError):
            self.rag.ingest_batch(self.key, [{"text": "replacement", "doc_id": "one"},
                                            {"text": "new two", "doc_id": "two"},
                                            {"text": "new three", "doc_id": "three"}])
        self.assertEqual(self.snapshot(), before)
        self.rag.ingest_batch(self.key, [{"text": "replacement", "doc_id": "one"},
                                        {"text": "new two", "doc_id": "two"}])
        self.assertEqual(self.rag.usage(self.key)["docs"], 2)
        self.assert_clean()

    def test_parent_standalone_replacement_at_full_quota(self):
        self.rag.index(self.key, "original flat text", "same")
        self.rag.index(self.key, "other document", "other")
        self.rag.ingest(self.key, "long chunked text " * 8, "same", max_chars=20, overlap=3)
        self.assertGreater(len(self.chunks()), 1)
        self.assertEqual(self.rag.usage(self.key)["docs"], 2)
        self.rag.index(self.key, "flat again", "same")
        self.assertEqual(self.chunks(), [])
        self.assertEqual(self.rag.store._con.execute("SELECT * FROM parent_docs").fetchall(), [])
        self.assertEqual(self.rag.usage(self.key)["docs"], 2)
        self.assert_clean()

    def test_parent_update_removes_obsolete_chunks(self):
        self.rag.ingest(self.key, "original long text " * 12, "same", max_chars=20, overlap=0)
        old_ids = {r[0] for r in self.chunks()}
        self.rag.ingest(self.key, "short text", "same")
        new_ids = {r[0] for r in self.chunks()}
        self.assertEqual(len(new_ids), 1)
        self.assertGreater(len(old_ids - new_ids), 0)
        self.assertEqual(self.rag.store.fetch_docs("a", list(old_ids - new_ids)), {})
        self.assert_clean()

    def test_delete_parent_is_tenant_scoped_and_frees_quota(self):
        for key in (self.key, "tenant-b-key"):
            self.rag.ingest(key, "tenant secret text " * 5, "shared", max_chars=20, overlap=2)
        other_before = self.chunks("b")
        self.assertTrue(self.rag.delete(self.key, "shared"))
        self.assertFalse(self.rag.delete(self.key, "shared"))
        self.assertEqual(self.chunks("a"), [])
        self.assertEqual(self.chunks("b"), other_before)
        self.assertEqual(self.rag.search(self.key, "secret"), [])
        self.assertEqual(self.rag.usage(self.key)["docs"], 0)
        self.assert_clean()

    def test_cannot_mutate_individual_chunk(self):
        self.rag.ingest(self.key, "chunk text", "parent")
        chunk_id = self.chunks()[0][0]
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.rag.delete(self.key, chunk_id)
        with self.assertRaises(ValueError):
            self.rag.index(self.key, "replacement", chunk_id)
        with self.assertRaises(ValueError):
            self.rag.ingest(self.key, "replacement", chunk_id)
        self.assertEqual(self.snapshot(), before)
        self.assert_clean()

    def test_generated_id_collision_rolls_back(self):
        generated = "chunk:" + hashlib.sha256(json.dumps(["parent", 0]).encode()).hexdigest()
        self.rag.index(self.key, "occupied", generated)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "collides"):
            self.rag.ingest(self.key, "new parent", "parent")
        self.assertEqual(self.snapshot(), before)
        self.assert_clean()

    def test_batch_parent_collides_with_generated_id_rolls_back(self):
        generated = "chunk:" + hashlib.sha256(json.dumps(["parent", 0]).encode()).hexdigest()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "collides"):
            self.rag.ingest_batch(self.key, [{"text": "first", "doc_id": "parent"},
                                            {"text": "second", "doc_id": generated}])
        self.assertEqual(self.snapshot(), before)
        self.assert_clean()

    def test_later_provider_batch_failure_preserves_live_index(self):
        self.rag.ingest(self.key, "original", "one")
        before = self.snapshot()
        self.embedder.fail_on = len(self.embedder.calls) + 2
        with self.assertRaisesRegex(RuntimeError, "provider failure"):
            self.rag.ingest_batch(self.key, [{"text": "abcdefghijklmno", "doc_id": "one"},
                                            {"text": "second text", "doc_id": "two"}],
                                  max_chars=5, overlap=0, batch_size=1)
        self.assertEqual(self.snapshot(), before)
        self.assert_clean()

    def test_fts_insert_failure_rolls_back_parent_and_identity(self):
        before = self.snapshot()
        con = self.rag.store._con
        con.set_authorizer(lambda action, table, *args: sqlite3.SQLITE_DENY
                           if action == sqlite3.SQLITE_INSERT and table == "docs_fts"
                           else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.rag.ingest(self.key, "new parent", "parent")
        finally:
            # Python 3.10 cannot reliably disable callbacks with None.
            con.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(self.snapshot(), before)
        self.assert_clean()
        self.rag.ingest(self.key, "new parent", "parent")
        self.assert_clean()

    def test_keyboard_interrupt_mid_write_rolls_back_everything(self):
        self.rag.ingest(self.key, "old parent", "parent")
        before = self.snapshot()
        original = self.rag._write_doc
        count = 0
        def interrupt(*args):
            nonlocal count
            original(*args)
            count += 1
            if count == 2:
                raise KeyboardInterrupt()
        with patch.object(self.rag, "_write_doc", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.rag.ingest(self.key, "abcdefghijklmno", "parent", max_chars=5, overlap=0)
        self.assertEqual(self.snapshot(), before)
        self.assert_clean()

    def test_same_tenant_concurrent_write_aborts_without_lost_update(self):
        with TenantRAG(self.path) as other:
            self.embedder.hook = lambda: other.index(self.key, "concurrent writer", "concurrent")
            with self.assertRaisesRegex(RuntimeError, "changed during ingestion"):
                self.rag.ingest(self.key, "new parent", "parent")
            self.assertEqual(self.rag.list_docs(self.key), [
                {"doc_id": "concurrent", "text": "concurrent writer", "metadata": {}}])
            self.assert_clean()

    def test_other_tenant_write_does_not_abort_batch(self):
        with TenantRAG(self.path) as other:
            self.embedder.hook = lambda: other.index("tenant-b-key", "concurrent writer", "concurrent")
            self.rag.ingest(self.key, "new parent", "parent")
            self.assertEqual(self.rag.usage(self.key)["docs"], 1)
            self.assertEqual(self.rag.usage("tenant-b-key")["docs"], 1)
            self.assert_clean()

    def test_empty_invalid_batch_and_auth_fail_before_embedding(self):
        self.assertEqual(self.rag.ingest_batch(self.key, []), [])
        for documents in (None, {}, [None], [{"text": ""}], [{"text": "ok", "source": 123}],
                          [{"text": "ok", "format": "html"}], [{"text": "ok", "metadata": []}]):
            with self.subTest(documents=documents), self.assertRaises(ValueError):
                self.rag.ingest_batch(self.key, documents)
        with self.assertRaises(AuthError):
            self.rag.ingest_batch("bad-key", [{"text": "ok"}])
        for size in (0, -1, True, 1025, "1"):
            with self.assertRaises(ValueError):
                self.rag.ingest_batch(self.key, [], batch_size=size)
        self.assertEqual(self.embedder.calls, [])
        self.assert_clean()

    def test_whitespace_only_chunks_omitted_for_strict_provider(self):
        text = "ab" + " \t\n" * 8 + "中文"
        original = self.embedder.embed
        def strict(texts):
            if any(not text.strip() for text in texts):
                raise ValueError("provider input texts must be non-empty")
            return original(texts)
        with patch.object(self.embedder, "embed", side_effect=strict):
            result = self.rag.ingest(self.key, text, "parent", max_chars=2, overlap=0)
        self.assertEqual(result["chunks"], 2)
        self.assertEqual(self.embedder.calls, [["ab", "中文"]])
        self.assertEqual(self.rag.list_docs(self.key)[0]["text"], text)
        for _, chunk, metadata, start, end in self.chunks():
            self.assertEqual(chunk, text[start:end])
            self.assertEqual(json.loads(metadata)["_chunk"]["start"], start)
        self.assert_clean()

    def test_amplified_overlap_rejected_before_provider(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "ingestion limits"):
            self.rag.ingest(self.key, "x" * 20_000, "parent", max_chars=10_000, overlap=9_999)
        self.assertEqual(self.embedder.calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_combined_batch_chunk_limit_rejected_before_provider(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "batch exceeds"):
            self.rag.ingest_batch(self.key, [{"text": "x" * 6_000, "doc_id": "one"},
                                            {"text": "y" * 6_000, "doc_id": "two"}],
                                  max_chars=1, overlap=0)
        self.assertEqual(self.embedder.calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_combined_batch_character_limit_rejected_before_provider(self):
        before = self.snapshot()
        text = "x" * 1_000_000
        with self.assertRaisesRegex(ValueError, "batch exceeds"):
            self.rag.ingest_batch(self.key, [{"text": text, "doc_id": str(i)} for i in range(11)],
                                  max_chars=1_000_000, overlap=0)
        self.assertEqual(self.embedder.calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_utf8_and_escaped_provenance_size_limits_before_provider(self):
        before = self.snapshot()
        invalid = [
            {"metadata": {"value": "中" * 21_846}},
            {"source": "中" * 5_462},
            {"source": "\n" * 8_192},
            {"doc_id": "中" * 1_366},
        ]
        for extra in invalid:
            with self.subTest(field=next(iter(extra))), self.assertRaisesRegex(ValueError, "size limit"):
                self.rag.ingest_batch(self.key, [{"text": "valid text", **extra}])
            self.assertEqual(self.embedder.calls, [])
            self.assertEqual(self.snapshot(), before)

    def test_repeated_metadata_and_source_budget_before_provider(self):
        before = self.snapshot()
        for extra, count in (({"metadata": {"value": "x" * 60_000}}, 200),
                             ({"source": "x" * 15_000}, 700)):
            with self.subTest(field=next(iter(extra))), self.assertRaisesRegex(ValueError, "metadata.*budget"):
                self.rag.ingest_batch(self.key, [{"text": "x" * count, "doc_id": "parent", **extra}],
                                      max_chars=1, overlap=0)
            self.assertEqual(self.embedder.calls, [])
            self.assertEqual(self.snapshot(), before)

    def test_combined_batch_provenance_budget_before_provider(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "metadata.*budget"):
            self.rag.ingest_batch(self.key, [
                {"text": "x" * 100, "doc_id": name, "metadata": {"v": "x" * 60_000}}
                for name in ("one", "two")], max_chars=1, overlap=0)
        self.assertEqual(self.embedder.calls, [])
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
