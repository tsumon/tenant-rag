"""高层 API：鉴权 → 配额 → 索引/检索/删除/用量。

隔离保证：
1. 所有入口先鉴权（api_key → tenant_id），拿不到 tenant_id 直接 AuthError；
2. Store 层每条 SQL 强制 tenant_id 过滤；
3. doc_id 是租户内命名空间（复合主键），跨租户同名互不干扰。
"""
from __future__ import annotations

import uuid
import hashlib
import json
import time

from .embed import Embedder, FakeEmbedder
from .search import hybrid_search, validate_fusion
from .store import IsolationError, Store, _pack
from .chunking import chunk_text
from .identity import EmbeddingIdentity


class AuthError(Exception):
    pass


class QuotaError(Exception):
    pass


class IngestionConflictError(RuntimeError):
    """Concurrent tenant changes require a fresh ingestion attempt."""


class TenantRAG:
    def __init__(self, path: str = "tenantrag.db", embedder: Embedder | None = None, *, embedding_identity=None):
        self.store = Store(path)
        self.embedder = embedder or FakeEmbedder()
        self.identity = EmbeddingIdentity(self.store, self.embedder, embedding_identity)

    def register(self, tenant_id: str, api_key: str, quota_docs: int = 10000) -> None:
        self.store.create_tenant(tenant_id, api_key, quota_docs)

    def _tenant(self, api_key: str) -> str:
        if not isinstance(api_key, str) or not api_key:
            raise AuthError("invalid api key")
        tid = self.store.resolve_tenant(api_key)
        if tid is None:
            raise AuthError("invalid api key")
        return tid

    @staticmethod
    def _validate_doc(text, doc_id, metadata):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        if doc_id is not None and (not isinstance(doc_id, str) or not doc_id):
            raise ValueError("doc_id must be a non-empty string")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")
        return json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True, allow_nan=False)

    def _write_doc(self, tid, doc_id, text, metadata_json, vec):
        blob, dim = _pack(vec)
        con = self.store._con
        con.execute(
            "INSERT INTO docs(tenant_id,doc_id,text,metadata,embedding,dim,created_at) VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(tenant_id,doc_id) DO UPDATE SET text=excluded.text,metadata=excluded.metadata,"
            "embedding=excluded.embedding,dim=excluded.dim",
            (tid, doc_id, text, metadata_json, blob, dim, time.time()))
        con.execute("DELETE FROM docs_fts WHERE tenant_id=? AND doc_id=?", (tid, doc_id))
        con.execute("INSERT INTO docs_fts(tenant_id,doc_id,text) VALUES(?,?,?)", (tid, doc_id, text))

    def index(self, api_key: str, text: str, doc_id: str | None = None,
              metadata: dict | None = None) -> str:
        tid = self._tenant(api_key)
        metadata_json = self._validate_doc(text, doc_id, metadata)
        doc_id = doc_id or uuid.uuid4().hex
        model, expected = self.identity.capture()
        self.identity.check(expected=expected)
        vectors = model.embed([text])
        dim = self.identity.validate(vectors, 1)
        with self.store.lock:
            con = self.store._con
            try:
                con.execute("BEGIN IMMEDIATE")
                self.identity.bind(dim, expected=expected)
                if con.execute("SELECT 1 FROM doc_chunks WHERE tenant_id=? AND doc_id=?", (tid, doc_id)).fetchone():
                    raise ValueError("replace the parent document, not an individual chunk")
                exists = self.store.doc_exists(tid, doc_id) or con.execute(
                    "SELECT 1 FROM parent_docs WHERE tenant_id=? AND parent_id=?", (tid, doc_id)).fetchone()
                if not exists and self.store.doc_count(tid) >= self.store.get_quota(tid):
                    raise QuotaError("document quota exceeded")
                self.store._delete_parent(tid, doc_id)
                self._write_doc(tid, doc_id, text, metadata_json, vectors[0])
                con.commit()
            except BaseException:
                con.rollback()
                raise
        return doc_id

    def _snapshot(self, tid):
        con = self.store._con
        return (con.execute("SELECT * FROM docs WHERE tenant_id=? ORDER BY doc_id", (tid,)).fetchall(),
                con.execute("SELECT * FROM parent_docs WHERE tenant_id=? ORDER BY parent_id", (tid,)).fetchall())

    def ingest(self, api_key: str, text: str, doc_id: str | None = None,
               metadata: dict | None = None, source: str | None = None,
               *, format: str = "text", max_chars: int = 1000, overlap: int = 100) -> dict:
        """Atomically replace a parent and its chunks. Offsets are Unicode characters."""
        return self.ingest_batch(api_key, [{"text": text, "doc_id": doc_id,
            "metadata": metadata, "source": source, "format": format}],
            max_chars=max_chars, overlap=overlap)[0]

    def ingest_batch(self, api_key: str, documents: list[dict], *,
                     max_chars: int = 1000, overlap: int = 100, batch_size: int = 64) -> list[dict]:
        """All-or-nothing incremental batch. Quota counts parents, not chunks.

        Duplicate explicit parent IDs in one batch are rejected. Identical parent
        retries skip writes/provider calls; repeated chunk texts share embeddings
        only inside this request. Concurrent tenant changes reject the batch.
        """
        tid = self._tenant(api_key)
        if not isinstance(documents, list) or any(not isinstance(d, dict) for d in documents):
            raise ValueError("documents must be a list of objects")
        if type(batch_size) is not int or not 1 <= batch_size <= 1024:
            raise ValueError("batch_size must be between 1 and 1024")
        chunk_text("", max_chars=max_chars, overlap=overlap)
        prepared, seen = [], set()
        total_chars = total_chunks = total_metadata = 0
        for d in documents:
            text, doc_id = d.get("text"), d.get("doc_id")
            metadata_json = self._validate_doc(text, doc_id, d.get("metadata"))
            source = d.get("source")
            if source is not None and not isinstance(source, str):
                raise ValueError("source must be a string or null")
            doc_id = doc_id or uuid.uuid4().hex
            if doc_id in seen:
                raise ValueError("duplicate doc_id within batch")
            seen.add(doc_id)
            fmt = d.get("format", "text")
            chunks = [c for c in chunk_text(text, max_chars=max_chars, overlap=overlap, format=fmt)
                      if c["text"].strip()]
            total_chars += sum(len(c["text"]) for c in chunks)
            total_chunks += len(chunks)
            if total_chars > 10_000_000 or total_chunks > 10_000:
                raise ValueError("batch exceeds 10,000 chunks or 10,000,000 chunk characters")
            metadata_bytes = len(metadata_json.encode("utf-8"))
            source_bytes = len(json.dumps(source, ensure_ascii=False).encode("utf-8"))
            id_bytes = len(json.dumps(doc_id, ensure_ascii=False).encode("utf-8"))
            if metadata_bytes > 65_536 or source_bytes > 16_384 or id_bytes > 4_096:
                raise ValueError("ingestion metadata, source or document ID exceeds size limit")
            # Conservative bound includes reserved chunk provenance fields.
            total_metadata += (metadata_bytes + source_bytes + id_bytes + 512) * len(chunks)
            if total_metadata > 10_000_000:
                raise ValueError("batch chunk metadata exceeds 10,000,000 byte budget")
            options = json.dumps({"version": 1, "format": fmt, "max_chars": max_chars, "overlap": overlap}, sort_keys=True)
            digest = hashlib.sha256(json.dumps([text, metadata_json, source, options], ensure_ascii=False).encode()).hexdigest()
            prepared.append(dict(doc_id=doc_id, text=text, metadata=metadata_json, source=source,
                                 options=options, hash=digest, chunks=chunks))
        if not prepared:
            return []
        model, expected = self.identity.capture()
        self.identity.check(expected=expected)
        with self.store.lock:
            snapshot = self._snapshot(tid)
            old = {r[1]: r for r in snapshot[1]}
            for d in prepared:
                d["unchanged"] = d["doc_id"] in old and old[d["doc_id"]][5] == d["hash"]
        # Reuse unchanged chunk text vectors within this tenant and model.
        from .store import _unpack
        cached = {r[2]: _unpack(r[4], r[5]) for r in snapshot[0] if r[4] is not None}
        texts = list(dict.fromkeys(c["text"] for d in prepared if not d["unchanged"] for c in d["chunks"] if c["text"] not in cached))
        vectors, dim = dict(cached), None
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            values = model.embed(batch)
            batch_dim = self.identity.validate(values, len(batch))
            if dim is not None and batch_dim != dim:
                raise ValueError("embedding dimension changed between batches")
            dim = batch_dim
            vectors.update(zip(batch, values))
        with self.store.lock:
            con = self.store._con
            try:
                con.execute("BEGIN IMMEDIATE")
                self.identity.check(expected=expected)
                if self._snapshot(tid) != snapshot:
                    raise IngestionConflictError("tenant documents changed during ingestion; retry")
                if dim is not None:
                    self.identity.bind(dim, expected=expected)
                existing = {r[1] for r in snapshot[0]} | set(old)
                additions = sum(d["doc_id"] not in existing for d in prepared)
                if self.store.doc_count(tid) + additions > self.store.get_quota(tid):
                    raise QuotaError("document quota exceeded")
                for d in prepared:
                    parent = d["doc_id"]
                    if con.execute("SELECT 1 FROM doc_chunks WHERE tenant_id=? AND doc_id=?", (tid, parent)).fetchone():
                        raise ValueError("parent ID collides with an existing chunk")
                    if d["unchanged"]:
                        continue
                    self.store._delete_parent(tid, parent)
                    con.execute("DELETE FROM docs_fts WHERE tenant_id=? AND doc_id=?", (tid, parent))
                    con.execute("DELETE FROM docs WHERE tenant_id=? AND doc_id=?", (tid, parent))
                    con.execute("INSERT INTO parent_docs VALUES(?,?,?,?,?,?,?,?)", (tid,parent,d["text"],d["metadata"],d["source"],d["hash"],d["options"],time.time()))
                    for i, c in enumerate(d["chunks"]):
                        chunk_id = "chunk:" + hashlib.sha256(json.dumps([parent, i], ensure_ascii=False).encode()).hexdigest()
                        if chunk_id in seen or con.execute("SELECT 1 FROM docs WHERE tenant_id=? AND doc_id=?", (tid, chunk_id)).fetchone() or con.execute("SELECT 1 FROM parent_docs WHERE tenant_id=? AND parent_id=?", (tid, chunk_id)).fetchone():
                            raise ValueError("generated chunk ID collides with an existing document")
                        meta = json.loads(d["metadata"])
                        meta["_chunk"] = {"parent_id":parent,"source":d["source"],"start":c["start"],"end":c["end"],"index":i}
                        self._write_doc(tid, chunk_id, c["text"], json.dumps(meta, ensure_ascii=False), vectors[c["text"]])
                        con.execute("INSERT INTO doc_chunks VALUES(?,?,?,?,?)", (tid,chunk_id,parent,c["start"],c["end"]))
                con.commit()
            except BaseException:
                con.rollback()
                raise
        return [{"doc_id":d["doc_id"],"chunks":len(d["chunks"]),"content_hash":d["hash"],
                 "unchanged":d["unchanged"]} for d in prepared]

    def adopt_legacy_embeddings(self):
        """Trusted operator assertion of legacy provenance, not model verification."""
        return self.identity.adopt_legacy()

    def reindex(self, embedder: Embedder, *, embedding_identity=None, batch_size=64, cancelled=None):
        """Trusted database-wide operation across all tenants; not an HTTP API."""
        result = self.identity.reindex(embedder, identity=embedding_identity,
                                       batch_size=batch_size, cancelled=cancelled)
        self.embedder = embedder
        return result

    def search(self, api_key: str, query: str, k: int = 10, *,
               fusion: str = 'rrf', lexical_weight: float | None = None) -> list[dict]:
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        tid = self._tenant(api_key)
        weight = validate_fusion(fusion, lexical_weight)
        model, expected = self.identity.capture()
        self.identity.check(expected=expected)
        vectors = model.embed([query])
        dim = self.identity.validate(vectors, 1)
        with self.store.lock:
            con = self.store._con
            try:
                con.execute("BEGIN")
                self.identity.check(dim, expected=expected)
                result = hybrid_search(self.store, tid, query, vectors[0], k=k,
                                       fusion=fusion, lexical_weight=weight)
                con.commit()
                return result
            except BaseException:
                con.rollback()
                raise

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

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self) -> None:
        self.store.close()
