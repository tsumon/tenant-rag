"""No provider IO: identity migration and failure-injected atomic reindex."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tenantrag.embed import FakeEmbedder, OpenAIEmbedder
from tenantrag.identity import (EmbeddingIdentity, EmbeddingIdentityError,
                                ReindexCancelled, ReindexConflictError)
from tenantrag.store import Store


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'index.db')
        self.store = Store(self.path)
        self.store.create_tenant('alice', 'alice-key')
        self.store.create_tenant('bob', 'bobs-key')
        self.model = FakeEmbedder(8)
        self.identity = EmbeddingIdentity(self.store, self.model)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def legacy(self):
        for tenant in ['alice', 'bob']:
            self.store.upsert_doc(tenant, 'shared', tenant + ' private text', {},
                                  self.model.embed([tenant])[0])

    def current(self):
        self.legacy()
        self.identity.adopt_legacy()

    def target(self):
        model = FakeEmbedder(8)
        model.embedding_identity['revision'] = 'new-revision'
        return model

    def state(self):
        return self.identity._snapshot(), self.identity.status()

    def test_empty_does_not_call_provider_and_bind_rolls_back(self):
        model = OpenAIEmbedder('dummy', base_url='http://localhost:9999/v1')
        with patch.object(model, 'embed', side_effect=AssertionError('unexpected network')):
            identity = EmbeddingIdentity(self.store, model)
            identity.check()
        self.store._con.execute('BEGIN IMMEDIATE')
        self.identity.bind(8)
        self.store._con.rollback()
        self.assertIsNone(self.identity.status())

    def test_legacy_requires_explicit_adoption(self):
        self.legacy()
        with self.assertRaises(EmbeddingIdentityError):
            self.identity.check()
        self.assertEqual(self.identity.adopt_legacy()['dimension'], 8)
        self.identity.check(8)
        self.assertEqual(self.store.fetch_docs('alice', ['shared'])['shared']['text'], 'alice private text')

    def test_same_dimension_different_model_rejected(self):
        self.current()
        other = EmbeddingIdentity(self.store, self.target())
        with self.assertRaises(EmbeddingIdentityError):
            other.check(8)
        with self.assertRaises(EmbeddingIdentityError):
            other.adopt_legacy()
        with self.assertRaises(EmbeddingIdentityError):
            self.identity.check(9)

    def test_reindex_updates_all_tenants_without_touching_fts(self):
        self.current()
        before = self.store._con.execute('SELECT * FROM docs_fts ORDER BY tenant_id').fetchall()
        stale = EmbeddingIdentity(self.store, self.model)
        self.assertEqual(self.identity.reindex(self.target(), batch_size=1), 2)
        self.identity.check(8)
        with self.assertRaises(EmbeddingIdentityError):
            stale.check()
        self.assertEqual(before, self.store._con.execute('SELECT * FROM docs_fts ORDER BY tenant_id').fetchall())
        self.assertEqual(set(self.store.fetch_docs('alice', ['shared'])), {'shared'})

    def test_provider_failure_and_keyboard_interrupt_keep_live_index(self):
        self.current()
        before = self.state()
        for failure in (RuntimeError('provider failed'), KeyboardInterrupt()):
            target = self.target()
            with patch.object(target, 'embed', side_effect=[[[1.0] * 8], failure]):
                with self.assertRaises(type(failure)):
                    self.identity.reindex(target, batch_size=1)
            self.assertEqual(before, self.state())
            self.assertFalse(self.store._con.in_transaction)

    def test_cancellation_after_sql_updates_rolls_back(self):
        self.current()
        before = self.state()
        count = 0
        def cancelled():
            nonlocal count
            count += 1
            return count == 5  # start, batch, pre-lock, pre-write, post-write
        with self.assertRaises(ReindexCancelled):
            self.identity.reindex(self.target(), cancelled=cancelled)
        self.assertEqual(before, self.state())
        self.assertFalse(self.store._con.in_transaction)
        self.assertEqual(self.identity.reindex(self.target()), 2)

    def test_database_failure_during_swap_rolls_back_all_rows(self):
        self.current()
        before = self.state()
        self.store._con.execute("CREATE TRIGGER reject_bob BEFORE UPDATE OF embedding ON docs "
                                "WHEN NEW.tenant_id='bob' BEGIN SELECT RAISE(ABORT, 'injected'); END")
        self.store._con.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.identity.reindex(self.target())
        self.assertEqual(before, self.state())
        self.assertFalse(self.store._con.in_transaction)

    def test_concurrent_writer_aborts_swap_and_preserves_new_data(self):
        self.current()
        before_config = self.identity.status()
        target = self.target()
        original = target.embed
        def embed(texts):
            with_other = Store(self.path)
            try:
                with_other.upsert_doc('alice', 'new', 'concurrent write', {}, [1.0] * 8)
            finally:
                with_other.close()
            return original(texts)
        with patch.object(target, 'embed', side_effect=embed):
            with self.assertRaises(ReindexConflictError):
                self.identity.reindex(target)
        self.assertEqual(before_config, self.identity.status())
        self.assertTrue(self.store.doc_exists('alice', 'new'))

    def test_malformed_legacy_and_invalid_vectors_refused(self):
        self.legacy()
        self.store._con.execute("UPDATE docs SET dim=9 WHERE tenant_id='bob'")
        self.store._con.commit()
        with self.assertRaises(EmbeddingIdentityError):
            self.identity.adopt_legacy()
        for vectors in ([], [[float('nan')]], [[True]], [[10 ** 1000]], [[1.0], [1.0, 2.0]]):
            with self.subTest(vectors=str(vectors)[:60]):
                with self.assertRaises(EmbeddingIdentityError):
                    self.identity.validate(vectors, 1 if len(vectors) < 2 else 2)

    def test_identity_explicit_for_custom_embedders(self):
        class Custom:
            pass
        identity = EmbeddingIdentity(self.store, Custom())
        with self.assertRaises(EmbeddingIdentityError):
            identity.check()
        EmbeddingIdentity(self.store, Custom(), {'provider': 'local', 'model': 'custom', 'revision': 'v1'}).check()

    def test_captured_identity_rejects_inflight_old_vectors(self):
        self.current()
        model, expected = self.identity.capture()
        vectors = model.embed(['old in-flight request'])
        self.identity.reindex(self.target())
        self.store._con.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(EmbeddingIdentityError):
                self.identity.bind(len(vectors[0]), expected=expected)
        finally:
            self.store._con.rollback()

    def test_inconsistent_reindex_batch_dimensions_leave_old_index(self):
        self.current()
        before = self.state()
        target = self.target()
        with patch.object(target, 'embed', side_effect=[[[1.0] * 8], [[1.0] * 9]]):
            with self.assertRaises(EmbeddingIdentityError):
                self.identity.reindex(target, batch_size=1)
        self.assertEqual(before, self.state())

    def test_keyboard_interrupt_inside_write_transaction_rolls_back(self):
        self.current()
        before = self.state()
        count = 0
        def cancelled():
            nonlocal count
            count += 1
            if count == 5:
                raise KeyboardInterrupt()
            return False
        with self.assertRaises(KeyboardInterrupt):
            self.identity.reindex(self.target(), cancelled=cancelled)
        self.assertEqual(before, self.state())
        self.assertFalse(self.store._con.in_transaction)

    def test_provider_identity_excludes_key_and_trailing_slash(self):
        a = OpenAIEmbedder('key-a', base_url='http://localhost:9999/v1/')
        b = OpenAIEmbedder('key-b', base_url='http://localhost:9999/v1')
        self.assertEqual(a.embedding_identity, b.embedding_identity)
        self.assertNotIn('key-a', str(a.embedding_identity))
        self.assertNotEqual(a.embedding_identity,
                            OpenAIEmbedder('key-a', revision='v2').embedding_identity)
