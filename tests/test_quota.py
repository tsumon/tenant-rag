"""配额测试：超限拒绝、更新不占用新配额。"""
import os
import tempfile
import unittest

from tenantrag import QuotaError, TenantRAG
from tenantrag.embed import FakeEmbedder


class QuotaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rag = TenantRAG(
            path=os.path.join(self.tmp.name, "t.db"), embedder=FakeEmbedder()
        )
        self.rag.register("t1", "test-key-1", quota_docs=2)

    def tearDown(self):
        self.rag.close()
        self.tmp.cleanup()

    def test_quota_enforced(self):
        self.rag.index("test-key-1", "doc one", doc_id="d1")
        self.rag.index("test-key-1", "doc two", doc_id="d2")
        with self.assertRaises(QuotaError):
            self.rag.index("test-key-1", "doc three", doc_id="d3")

    def test_update_existing_does_not_consume_quota(self):
        self.rag.index("test-key-1", "doc one", doc_id="d1")
        self.rag.index("test-key-1", "doc two", doc_id="d2")
        self.rag.index("test-key-1", "doc one updated", doc_id="d1")  # 更新，不抛
        self.assertEqual(self.rag.usage("test-key-1")["docs"], 2)

    def test_delete_frees_quota(self):
        self.rag.index("test-key-1", "doc one", doc_id="d1")
        self.rag.index("test-key-1", "doc two", doc_id="d2")
        self.rag.delete("test-key-1", "d1")
        self.rag.index("test-key-1", "doc three", doc_id="d3")  # 删后可再加
        self.assertEqual(self.rag.usage("test-key-1")["docs"], 2)


if __name__ == "__main__":
    unittest.main()
