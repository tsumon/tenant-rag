"""租户隔离测试：商业级 claim 的核心证据。

每条测试都在回答同一个问题：租户 A 有没有可能看到/改到租户 B 的数据？
"""
import os
import tempfile
import unittest

from tenantrag import AuthError, TenantRAG
from tenantrag.embed import FakeEmbedder


class IsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rag = TenantRAG(
            path=os.path.join(self.tmp.name, "t.db"),
            embedder=FakeEmbedder(),
        )
        self.rag.register("tenant-a", "test-key-a")
        self.rag.register("tenant-b", "test-key-b")
        # 两租户存完全相同的文本：隔离必须靠 tenant_id，不能靠内容去重
        self.rag.index("test-key-a", "Netflix 每月 198 元自动续费", doc_id="doc-1")
        self.rag.index("test-key-b", "Netflix 每月 198 元自动续费", doc_id="doc-1-b")
        self.rag.index("test-key-b", "iCloud 每月 21 元", doc_id="doc-2-b")

    def tearDown(self):
        self.rag.close()
        self.tmp.cleanup()

    def test_search_never_returns_other_tenant_docs(self):
        hits = self.rag.search("test-key-a", "Netflix 续费多少钱")
        ids = [h["doc_id"] for h in hits]
        self.assertIn("doc-1", ids)
        self.assertNotIn("doc-1-b", ids)
        self.assertNotIn("doc-2-b", ids)

    def test_search_other_tenant_unique_term_invisible(self):
        # B 有 A 完全没有的词：A 搜这个词，结果里绝不能出现 B 的 doc
        # （注：不断言空结果——向量通道可能返回 A 自己的低分文档，
        # 那是相关性问题，不是隔离问题）
        hits = self.rag.search("test-key-a", "iCloud")
        ids = [h["doc_id"] for h in hits]
        self.assertNotIn("doc-1-b", ids)
        self.assertNotIn("doc-2-b", ids)

    def test_delete_other_tenant_doc_is_noop(self):
        self.assertFalse(self.rag.delete("test-key-a", "doc-2-b"))
        # B 的数据还在
        hits = self.rag.search("test-key-b", "iCloud")
        self.assertEqual([h["doc_id"] for h in hits], ["doc-2-b"])

    def test_same_doc_id_is_per_tenant_namespace(self):
        # M3 语义修正：doc_id 是租户内命名空间（复合主键）。
        # A 和 B 都用 "shared" 存各自的文档：互不干扰，各自读写自己的。
        self.rag.store.upsert_doc("tenant-a", "shared", "A 的版本", None, [0.1] * 256)
        self.rag.store.upsert_doc("tenant-b", "shared", "B 的版本", None, [0.1] * 256)
        docs_a = self.rag.store.fetch_docs("tenant-a", ["shared"])
        docs_b = self.rag.store.fetch_docs("tenant-b", ["shared"])
        self.assertEqual(docs_a["shared"]["text"], "A 的版本")
        self.assertEqual(docs_b["shared"]["text"], "B 的版本")
        # A 更新自己的不影响 B
        self.rag.store.upsert_doc("tenant-a", "shared", "A 的新版本", None, [0.1] * 256)
        self.assertEqual(
            self.rag.store.fetch_docs("tenant-b", ["shared"])["shared"]["text"],
            "B 的版本",
        )

    def test_wrong_key_rejected(self):
        with self.assertRaises(AuthError):
            self.rag.search("wrong-key", "Netflix")
        with self.assertRaises(AuthError):
            self.rag.index("wrong-key", "hello")

    def test_usage_is_tenant_scoped(self):
        self.assertEqual(self.rag.usage("test-key-a")["docs"], 1)
        self.assertEqual(self.rag.usage("test-key-b")["docs"], 2)

    def test_store_level_queries_require_tenant(self):
        # 直调 Store 层：未知租户查不到任何东西
        self.assertEqual(self.rag.store.vector_search("no-such-tenant", [0.1] * 256), [])
        self.assertEqual(self.rag.store.fts_search("no-such-tenant", '"Netflix"'), [])
        self.assertEqual(self.rag.store.fetch_docs("no-such-tenant", ["doc-1"]), {})


if __name__ == "__main__":
    unittest.main()
