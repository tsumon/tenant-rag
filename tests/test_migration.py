"""M1→M3 schema 迁移测试：老库（doc_id 全局主键）打开后自动迁移。"""
import os
import sqlite3
import tempfile
import unittest

from tenantrag import TenantRAG
from tenantrag.embed import FakeEmbedder


OLD_SCHEMA = """
CREATE TABLE tenants(
  tenant_id    TEXT PRIMARY KEY,
  api_key_hash TEXT NOT NULL UNIQUE,
  quota_docs   INTEGER NOT NULL DEFAULT 10000,
  created_at   REAL NOT NULL
);
CREATE TABLE docs(
  doc_id     TEXT PRIMARY KEY,
  tenant_id  TEXT NOT NULL,
  text       TEXT NOT NULL,
  metadata   TEXT NOT NULL DEFAULT '{}',
  embedding  BLOB,
  dim        INTEGER,
  created_at REAL NOT NULL
);
CREATE VIRTUAL TABLE docs_fts USING fts5(doc_id UNINDEXED, text, tokenize='trigram');
"""


class MigrationTest(unittest.TestCase):
    def test_v1_to_v2_migration(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "old.db")
        con = sqlite3.connect(db)
        con.executescript(OLD_SCHEMA)
        import hashlib, time
        kh = hashlib.sha256(b"test-key-a").hexdigest()
        con.execute(
            "INSERT INTO tenants VALUES(?,?,?,?)", ("tenant-a", kh, 100, time.time())
        )
        con.execute(
            "INSERT INTO docs(doc_id, tenant_id, text, created_at)"
            " VALUES(?,?,?,?)",
            ("d1", "tenant-a", "腾讯视频 VIP 年费 198 元", time.time()),
        )
        con.execute("INSERT INTO docs_fts(doc_id, text) VALUES(?,?)",
                    ("d1", "腾讯视频 VIP 年费 198 元"))
        con.commit()
        con.close()

        # 用新代码打开：触发迁移
        rag = TenantRAG(db, embedder=FakeEmbedder())
        # Legacy rows remain readable but require explicit reindex before vector use.
        self.assertEqual(rag.list_docs("test-key-a")[0]["doc_id"], "d1")
        rag.reindex(FakeEmbedder())
        hits = rag.search("test-key-a", "腾讯视频多少钱")
        self.assertEqual(hits[0]["doc_id"], "d1")
        # 新语义可用：同租户覆盖、doc_id 租户内唯一
        rag.index("test-key-a", "腾讯视频新价格", doc_id="d1")
        hits = rag.search("test-key-a", "腾讯视频多少钱")
        self.assertIn("新价格", hits[0]["text"])
        rag.close()


if __name__ == "__main__":
    unittest.main()
