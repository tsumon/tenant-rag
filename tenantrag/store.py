"""SQLite 存储：文档表 + FTS5 全文索引 + 向量 blob。

隔离模型（MVP）：单库逻辑隔离——每一行带 tenant_id，每一条查询强制
`WHERE tenant_id = ?`。FTS5 表也存 tenant_id（UNINDEXED 列），全文检索
直接按租户过滤，不再需要 JOIN（M3 前是 JOIN docs，见 git 历史）。

doc_id 是租户内命名空间：(tenant_id, doc_id) 复合主键，两个租户可以用
相同的 doc_id（M3 dogfood 逼出的修正：两人都叫 "bill-txvideo" 很自然）。

升级路径：每租户独立 SQLite 文件（物理隔离），见 README。
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import threading
import time


class IsolationError(Exception):
    """试图跨租户读写他人数据。"""


SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants(
  tenant_id    TEXT PRIMARY KEY,
  api_key_hash TEXT NOT NULL UNIQUE,
  quota_docs   INTEGER NOT NULL DEFAULT 10000,
  created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS docs(
  tenant_id  TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  doc_id     TEXT NOT NULL,
  text       TEXT NOT NULL,
  metadata   TEXT NOT NULL DEFAULT '{}',
  embedding  BLOB,
  dim        INTEGER,
  created_at REAL NOT NULL,
  PRIMARY KEY (tenant_id, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_docs_tenant ON docs(tenant_id);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts
  USING fts5(tenant_id UNINDEXED, doc_id UNINDEXED, text, tokenize='trigram');
-- 注：trigram 是 CJK 的标准切法（unicode61 会把"腾讯视频"切成一个整词，
-- 单字查不到）。代价是索引体积变大约 3 倍；2 字以下查询走向量通道，见 README。
-- tenant_id 进 FTS 表后，全文检索可直接按租户过滤，不再 JOIN docs。
CREATE TABLE IF NOT EXISTS usage_meter(
  tenant_id TEXT NOT NULL,
  day       TEXT NOT NULL,
  requests  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, day)
);
"""


def _pack(vec: list[float]) -> tuple[bytes, int]:
    return struct.pack(f"<{len(vec)}f", *vec), len(vec)


def _unpack(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    """真正的余弦相似度（带归一化）。

    之前版本假设输入已归一化、直接点积——FakeEmbedder 成立，
    但 OpenAIEmbedder 返回的向量并未归一化，会导致排序错误
    （例：[2,2] 的点积大于 [1,0]，但余弦明明是后者大）。
    归一化这点开销在个人级语料上可忽略，正确性优先。
    """
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class Store:
    def __init__(self, path: str = "tenantrag.db"):
        self.path = path
        # RLock：Store 自己保证线程安全。写操作串行；读操作走 WAL 可并发。
        # service 层做"检查配额→写入"复合操作时也持这把锁，保证原子性。
        # server.py 不再需要全局锁（M2 的全局锁还顺带把 embedding 网络
        # 调用串行化了，一个慢请求堵死全服——修掉了）。
        self.lock = threading.RLock()
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA busy_timeout=5000")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._con.executescript(SCHEMA)
        self._migrate_v2()
        # 注意：check_same_thread=False 只关掉"跨线程使用"的检查，
        # 并不让并发 execute() 变安全——CPython 的 sqlite3 连接对象
        # 本身不支持多线程同时 execute（会报 InterfaceError:
        # bad parameter or other API misuse，Round 3 实测）。
        # 所以下面所有 self._con 的访问（读和写）都必须持 self.lock，
        # 读也不例外。WAL 的读不阻塞写优势因此部分让渡，但个人级
        # 语料下正确性优先，开销可忽略。

    def _migrate_v2(self) -> None:
        """M1→M3 迁移：docs 主键 doc_id → (tenant_id, doc_id)；
        docs_fts 加 tenant_id 列。已是新 schema 时为 no-op。
        整个迁移包在显式事务里：中途失败整体回滚，下次启动重试——
        不回滚的话，RENAME 后崩溃会导致数据孤儿在 docs_old，
        而 SCHEMA 已建了空的新 docs 表，迁移检测直接跳过
        （Round 6 实测：doc_count=0，静默丢数据）。
        注意不用 executescript：它会隐式 commit，破坏外层事务。"""
        con = self._con
        con.execute("BEGIN")
        try:
            cols = {r[1]: r[5] for r in con.execute("PRAGMA table_info(docs)")}
            if cols.get("doc_id") == 1 and cols.get("tenant_id") == 0:
                # 老库：doc_id 是全局主键
                con.execute("ALTER TABLE docs RENAME TO docs_old")
                con.execute(
                    "CREATE TABLE docs("
                    "  tenant_id  TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,"
                    "  doc_id     TEXT NOT NULL,"
                    "  text       TEXT NOT NULL,"
                    "  metadata   TEXT NOT NULL DEFAULT '{}',"
                    "  embedding  BLOB,"
                    "  dim        INTEGER,"
                    "  created_at REAL NOT NULL,"
                    "  PRIMARY KEY (tenant_id, doc_id))"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_docs_tenant ON docs(tenant_id)"
                )
                con.execute(
                    "INSERT INTO docs(tenant_id, doc_id, text, metadata, embedding, dim, created_at)"
                    " SELECT tenant_id, doc_id, text, metadata, embedding, dim, created_at"
                    " FROM docs_old"
                )
                con.execute("DROP TABLE docs_old")
            fts_cols = [r[1] for r in con.execute("PRAGMA table_info(docs_fts)")]
            if fts_cols and "tenant_id" not in fts_cols:
                con.execute("DROP TABLE docs_fts")
                con.execute(
                    "CREATE VIRTUAL TABLE docs_fts USING fts5("
                    "tenant_id UNINDEXED, doc_id UNINDEXED, text, tokenize='trigram')"
                )
                con.execute(
                    "INSERT INTO docs_fts(tenant_id, doc_id, text)"
                    " SELECT tenant_id, doc_id, text FROM docs"
                )
            con.commit()
        except Exception:
            con.rollback()
            raise

    # ---- 租户 ----
    @staticmethod
    def _key_hash(api_key: str) -> str:
        return hashlib.sha256(api_key.encode()).hexdigest()

    def create_tenant(self, tenant_id: str, api_key: str, quota_docs: int = 10000) -> None:
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("tenant_id 必须是非空字符串")
        if not api_key or not isinstance(api_key, str):
            raise ValueError("api_key 必须是非空字符串")
        if len(api_key) < 8:
            # api_key 是唯一的鉴权因子（key = tenant 身份），弱 key
            # 让整个隔离模型形同虚设。注册时直接拦，比文档里写
            # "请用强 key" 有效——没人看文档，但人人会撞到报错。
            # 8 位随机 keyspace ~2e14，HTTP 爆破不可行；8 以下基本
            # 是人可猜的（"1"、"test"、"k"）。
            raise ValueError("api_key 太短：至少 8 个字符，请用不可猜测的随机字符串")
        if not isinstance(quota_docs, int) or quota_docs < 0:
            raise ValueError("quota_docs 必须是非负整数")
        with self.lock:
            if self._con.execute(
                "SELECT 1 FROM tenants WHERE tenant_id=?", (tenant_id,)
            ).fetchone():
                raise ValueError(f"tenant_id {tenant_id!r} already exists")
            if self._con.execute(
                "SELECT 1 FROM tenants WHERE api_key_hash=?",
                (self._key_hash(api_key),),
            ).fetchone():
                raise ValueError("api_key 已被其他租户使用")
            self._con.execute(
                "INSERT INTO tenants(tenant_id, api_key_hash, quota_docs, created_at)"
                " VALUES(?,?,?,?)",
                (tenant_id, self._key_hash(api_key), quota_docs, time.time()),
            )
            self._con.commit()

    def resolve_tenant(self, api_key: str) -> str | None:
        with self.lock:
            row = self._con.execute(
                "SELECT tenant_id FROM tenants WHERE api_key_hash=?",
                (self._key_hash(api_key),),
            ).fetchone()
            return row[0] if row else None

    def get_quota(self, tenant_id: str) -> int | None:
        with self.lock:
            row = self._con.execute(
                "SELECT quota_docs FROM tenants WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
            return row[0] if row else None

    def doc_count(self, tenant_id: str) -> int:
        with self.lock:
            row = self._con.execute(
                "SELECT COUNT(*) FROM docs WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
            return row[0]

    def doc_exists(self, tenant_id: str, doc_id: str) -> bool:
        with self.lock:
            row = self._con.execute(
                "SELECT 1 FROM docs WHERE tenant_id=? AND doc_id=?", (tenant_id, doc_id)
            ).fetchone()
            return row is not None

    # ---- 文档 ----
    def upsert_doc(
        self,
        tenant_id: str,
        doc_id: str,
        text: str,
        metadata: dict | None = None,
        embedding: list[float] | None = None,
    ) -> None:
        # doc_id 是租户内命名空间：(tenant_id, doc_id) 复合主键，
        # 跨租户同名 doc_id 互不干扰，无需 IsolationError。
        # 整个 upsert（含 FTS 同步）在同一把锁内：单连接不支持并发
        # execute，写一半被读线程穿插会报 InterfaceError（Round 3）。
        with self.lock:
            if embedding is not None:
                blob, dim = _pack(embedding)
            else:
                blob, dim = None, None
            try:
                self._con.execute(
                    "INSERT INTO docs(tenant_id, doc_id, text, metadata, embedding, dim, created_at)"
                    " VALUES(?,?,?,?,?,?,?)"
                    " ON CONFLICT(tenant_id, doc_id) DO UPDATE SET"
                    "  text=excluded.text, metadata=excluded.metadata,"
                    "  embedding=excluded.embedding, dim=excluded.dim",
                    (
                        tenant_id,
                        doc_id,
                        text,
                        json.dumps(metadata or {}, ensure_ascii=False),
                        blob,
                        dim,
                        time.time(),
                    ),
                )
                # FTS 同步：先删后插，保证与 docs 一致
                self._con.execute(
                    "DELETE FROM docs_fts WHERE tenant_id=? AND doc_id=?",
                    (tenant_id, doc_id),
                )
                self._con.execute(
                    "INSERT INTO docs_fts(tenant_id, doc_id, text) VALUES(?,?,?)",
                    (tenant_id, doc_id, text),
                )
                self._con.commit()
            except Exception:
                # docs 写成功但 FTS 失败时不能留半提交，否则文档在向量
                # 通道可见、FTS 通道不可见，且开事务会一直占着写锁。
                self._con.rollback()
                raise

    def delete_doc(self, tenant_id: str, doc_id: str) -> bool:
        with self.lock:
            try:
                cur = self._con.execute(
                    "DELETE FROM docs WHERE tenant_id=? AND doc_id=?", (tenant_id, doc_id)
                )
                if cur.rowcount:
                    self._con.execute(
                        "DELETE FROM docs_fts WHERE tenant_id=? AND doc_id=?",
                        (tenant_id, doc_id),
                    )
                # miss 时也要 commit：0 行的 DELETE 同样开过隐式事务，
                # 不收尾会一直占着 RESERVED 写锁，挡住其他连接的写。
                self._con.commit()
                return cur.rowcount > 0
            except Exception:
                self._con.rollback()
                raise

    # ---- 检索（全部强制 tenant 过滤） ----
    def fts_search(
        self, tenant_id: str, fts_query: str, k: int = 50
    ) -> list[tuple[str, float]]:
        """返回 [(doc_id, bm25)]，bm25 越小越相关。
        FTS 表自带 tenant_id 列，直接过滤（M3 前是 JOIN docs，见模块 docstring）。"""
        if not fts_query:
            return []
        with self.lock:
            rows = self._con.execute(
                "SELECT doc_id, bm25(docs_fts) AS b FROM docs_fts"
                " WHERE docs_fts MATCH ? AND tenant_id = ?"
                " ORDER BY b LIMIT ?",
                (fts_query, tenant_id, k),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def vector_search(
        self, tenant_id: str, query_vec: list[float], k: int = 50
    ) -> list[tuple[str, float]]:
        with self.lock:
            rows = self._con.execute(
                "SELECT doc_id, embedding, dim FROM docs"
                " WHERE tenant_id=? AND embedding IS NOT NULL",
                (tenant_id,),
            ).fetchall()
        scored: list[tuple[str, float]] = []
        qdim = len(query_vec)
        for doc_id, blob, dim in rows:
            if dim != qdim:
                continue  # embedding 维度变更时的脏数据直接跳过
            scored.append((doc_id, _cosine(query_vec, _unpack(blob, dim))))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def fetch_docs(self, tenant_id: str, doc_ids: list[str]) -> dict[str, dict]:
        if not doc_ids:
            return {}
        q = ",".join("?" for _ in doc_ids)
        with self.lock:
            rows = self._con.execute(
                f"SELECT doc_id, text, metadata FROM docs"
                f" WHERE tenant_id=? AND doc_id IN ({q})",
                (tenant_id, *doc_ids),
            ).fetchall()
        return {
            r[0]: {"doc_id": r[0], "text": r[1], "metadata": json.loads(r[2])}
            for r in rows
        }

    def list_docs(self, tenant_id: str, limit: int = 1000, offset: int = 0) -> list[dict]:
        """列出某租户的全部文档（聚合类需求用；检索类走 search）。
        支持 limit/offset 分页，避免超大租户被静默截断。"""
        limit = max(1, min(int(limit), 10000))
        offset = max(0, int(offset))
        with self.lock:
            rows = self._con.execute(
                "SELECT doc_id, text, metadata FROM docs"
                " WHERE tenant_id=? ORDER BY rowid LIMIT ? OFFSET ?",
                (tenant_id, limit, offset),
            ).fetchall()
        return [
            {"doc_id": r[0], "text": r[1], "metadata": json.loads(r[2])}
            for r in rows
        ]

    # ---- 用量计量 ----
    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.localtime())
    def log_request(self, tenant_id: str) -> None:
        day = self._today()
        with self.lock:
            self._con.execute(
                "INSERT INTO usage_meter(tenant_id, day, requests) VALUES(?,?,1)"
                " ON CONFLICT(tenant_id, day) DO UPDATE SET requests=requests+1",
                (tenant_id, day),
            )
            self._con.commit()

    def request_count(self, tenant_id: str, day: str | None = None) -> int:
        with self.lock:
            row = self._con.execute(
                "SELECT requests FROM usage_meter WHERE tenant_id=? AND day=?",
                (tenant_id, day or self._today()),
            ).fetchone()
            return row[0] if row else 0

    def purge_usage(self, keep_days: int = 90) -> int:
        """删除 keep_days 天之前的 usage_meter 行，返回删除行数。
        usage_meter 按 (tenant, day) 一行无限增长：1000 租户一年
        约 36 万行，SQLite 无压力；但长期运行建议定期调用清理
        （如 cron 每月一次），避免十年后变成千万级小表。"""
        cutoff = time.strftime(
            "%Y-%m-%d", time.localtime(time.time() - keep_days * 86400)
        )
        with self.lock:
            cur = self._con.execute(
                "DELETE FROM usage_meter WHERE day < ?", (cutoff,)
            )
            self._con.commit()
            return cur.rowcount

    def close(self) -> None:
        self._con.close()
