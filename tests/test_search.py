"""混合检索功能测试：两路召回 + RRF 融合的行为。"""
import os
import tempfile
import unittest

from tenantrag import TenantRAG
from tenantrag.embed import FakeEmbedder
from tenantrag.search import build_fts_query, rrf


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rag = TenantRAG(
            path=os.path.join(self.tmp.name, "t.db"), embedder=FakeEmbedder()
        )
        self.rag.register("t1", "test-key-1")
        self.rag.index("test-key-1", "腾讯视频 VIP 年费 198 元，自动续费", doc_id="v-tx")
        self.rag.index("test-key-1", "网易云音乐黑胶会员月费 15 元", doc_id="m-163")
        self.rag.index("test-key-1", "ChatGPT Plus 每月 20 美元", doc_id="ai-gpt")

    def tearDown(self):
        self.rag.close()
        self.tmp.cleanup()

    def test_hybrid_finds_exact_term(self):
        hits = self.rag.search("test-key-1", "腾讯视频多少钱")
        self.assertEqual(hits[0]["doc_id"], "v-tx")
        self.assertIn("fts", hits[0]["matched"])

    def test_result_shape(self):
        hits = self.rag.search("test-key-1", "音乐会员", k=2)
        self.assertLessEqual(len(hits), 2)
        for h in hits:
            self.assertIn("doc_id", h)
            self.assertIn("text", h)
            self.assertIn("score", h)
            self.assertIn("matched", h)
            self.assertTrue(set(h["matched"]) <= {"vector", "fts"})

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.rag.search("test-key-1", "???"), [])

    def test_fts_query_escapes_special_chars(self):
        # 用户输入含 FTS5 保留字/符号不能抛异常
        q = build_fts_query('腾讯视频 AND "OR" (test)')
        self.assertIsNotNone(q)
        self.assertNotIn(" AND ", q)  # AND 被当成普通 token 处理
        hits = self.rag.search("test-key-1", '腾讯视频 AND "OR" (test)')
        self.assertTrue(any(h["doc_id"] == "v-tx" for h in hits))

    def test_rrf_prefers_hits_in_both_channels(self):
        fused = rrf([["a", "b"], ["b", "c"]])
        ids = [d for d, _ in fused]
        self.assertEqual(ids[0], "b")  # 两路都命中的排第一


if __name__ == "__main__":
    unittest.main()
