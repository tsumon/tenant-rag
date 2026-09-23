"""高层 API：鉴权 → 配额 → 索引/检索/删除/用量。

隔离保证：
1. 所有入口先鉴权（api_key → tenant_id），拿不到 tenant_id 直接 AuthError；
2. Store 层每条 SQL 强制 tenant_id 过滤；
3. doc_id 是租户内命名空间（复合主键），跨租户同名互不干扰。
"""
from __future__ import annotations

import uuid

from .embed import Embedder, FakeEmbedder
from .search import hybrid_search
from .store import IsolationError, Store


class AuthError(Exception):
    pass


class QuotaError(Exception):
    pass


class TenantRAG:
    def __init__(self, path: str = "tenantrag.db", embedder: Embedder | None = None):
        self.store = Store(path)
        self.embedder = embedder or FakeEmbedder()

    def register(self, tenant_id: str, api_key: str, quota_docs: int = 10000) -> None:
        self.store.create_tenant(tenant_id, api_key, quota_docs)

    def _tenant(self, api_key: str) -> str:
        if not isinstance(api_key, str) or not api_key:
            raise AuthError("invalid api key")
        tid = self.store.resolve_tenant(api_key)
        if tid is None:
            raise AuthError("invalid api key")
        return tid

    def index(
        self,
        api_key: str,
        text: str,
        doc_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        tid = self._tenant(api_key)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        if doc_id is not None and not isinstance(doc_id, str):
            raise ValueError("doc_id must be a string")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")
        doc_id = doc_id or uuid.uuid4().hex
        # embedding 是网络 IO，不持锁；配额检查+写入持锁，保证原子性
        #（upsert_doc 内部也会加锁，RLock 可重入）。
        vec = self.embedder.embed([text])[0]
        with self.store.lock:
            con = self.store._con
            # BEGIN IMMEDIATE：把"配额检查→写入"做成跨连接原子。
            # Store 的 RLock 只协调同一实例内的线程；同一进程两个
            # TenantRAG 实例打开同一 DB 文件时，各自的锁互不相干，
            # 不加这个并发下配额会超卖（Round 3 实测：quota=30 写出 31）。
            # IMMEDIATE 直接拿 RESERVED 写锁直到 commit，另一个连接的
            # BEGIN IMMEDIATE 会被 busy_timeout 挡住排队，而不是两个
            # 都通过配额检查。异常时 rollback，不留半开事务。
            if not con.in_transaction:
                con.execute("BEGIN IMMEDIATE")
                ours = True
            else:
                ours = False  # 防御：正常流程到这里不可能有开事务
            try:
                quota = self.store.get_quota(tid)
                if (
                    quota is not None
                    and not self.store.doc_exists(tid, doc_id)
                    and self.store.doc_count(tid) >= quota
                ):
                    raise QuotaError(f"tenant {tid} quota exceeded: {quota} docs")
                self.store.upsert_doc(tid, doc_id, text, metadata, vec)
            except Exception:
                if ours:
                    con.rollback()
                raise
        return doc_id

    def search(self, api_key: str, query: str, k: int = 10) -> list[dict]:
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        tid = self._tenant(api_key)
        qvec = self.embedder.embed([query])[0]
        return hybrid_search(self.store, tid, query, qvec, k=k)

    def delete(self, api_key: str, doc_id: str) -> bool:
        return self.store.delete_doc(self._tenant(api_key), doc_id)

    def usage(self, api_key: str) -> dict:
        tid = self._tenant(api_key)
        return {
            "tenant_id": tid,
            "docs": self.store.doc_count(tid),
            "quota_docs": self.store.get_quota(tid),
            # 与 HTTP /v1/usage 对齐：Python 版也返回 requests_today。
            # rate_limit 是 HTTP 限流器的属性，Python API 没有限流概念，不返回。
            # 注意：HTTP 版每次请求都会 log_request（含本次），Python 版不计量，
            # 所以两边的 requests_today 语义差一次调用——文档里说明。
            "requests_today": self.store.request_count(tid),
        }

    def list_docs(self, api_key: str, limit: int = 1000, offset: int = 0) -> list[dict]:
        """列出本租户文档。聚合类 Agent 需求（汇总/最贵/列表）走这里，
        问答类走 search —— RAG 应用的真实分工。
        支持 limit/offset 分页；调用方需要全量时应循环取完。"""
        return self.store.list_docs(self._tenant(api_key), limit, offset)

    def close(self) -> None:
        self.store.close()
