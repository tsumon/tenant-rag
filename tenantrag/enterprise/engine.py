"""EnterpriseTenantRAG orchestrator connecting ControlPlane, TenantPool, Retrieval, Rerank, Jobs, Chat, and Eval."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any

from tenantrag.embed import Embedder, FakeEmbedder, OpenAIEmbedder

from .chat import GroundedChatEngine
from .control_plane import ControlPlane, validate_tenant_id
from .errors import ForbiddenError, NotFoundError
from .jobs import AsyncJobManager
from .observability import DynamicRateLimiter, MetricsRegistry
from .parsers import parse_document_bytes
from .rerank import APIReranker, BaseReranker, LexicalCrossReranker, NoOpReranker
from .retrieval import enterprise_search, evaluate_acl
from .tenant_pool import TenantNode, TenantPool


class EnterpriseTenantRAG:
    """Thread-safe enterprise coordinator for multi-tenant physical SQLite RAG."""

    def __init__(
        self,
        data_dir: str | Path = "./enterprise_data",
        *,
        embedder: Embedder | None = None,
        embedding_identity: dict[str, str] | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
        rerank_base_url: str | None = None,
        rerank_api_key: str | None = None,
        rerank_model: str | None = None,
        read_pool_size: int = 4,
        job_workers: int = 2,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or self._embedder_from_env()
        self.embedding_identity = embedding_identity
        self.control_plane = ControlPlane(str(self.data_dir / "control_plane.db"))
        self.pool = TenantPool(
            self.data_dir,
            self.embedder,
            embedding_identity=embedding_identity,
            read_pool_size=read_pool_size,
        )
        self.jobs = AsyncJobManager(self.control_plane, worker_count=job_workers)
        self.chat = GroundedChatEngine(
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
        )
        self.metrics = MetricsRegistry()
        self.limiter = DynamicRateLimiter()
        self.rerank_base_url = (rerank_base_url or os.environ.get("RERANK_BASE_URL") or "").strip() or None
        self.rerank_api_key = (rerank_api_key or os.environ.get("RERANK_API_KEY") or "").strip() or None
        self.rerank_model = (rerank_model or os.environ.get("RERANK_MODEL") or "bge-reranker-v2-m3").strip()

    @staticmethod
    def _embedder_from_env() -> Embedder:
        base_url = (os.environ.get("EMBED_BASE_URL") or "").strip()
        if not base_url:
            return FakeEmbedder()
        api_key = os.environ.get("EMBED_API_KEY", "local-demo")
        model = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
        revision = os.environ.get("EMBED_REVISION", "unspecified")
        return OpenAIEmbedder(api_key=api_key, base_url=base_url, model=model, revision=revision)

    def resolve_reranker(self, mode: str = "lexical") -> BaseReranker:
        m = (mode or "none").strip().lower()
        if m == "none":
            return NoOpReranker()
        if m == "lexical":
            return LexicalCrossReranker()
        if m == "api":
            if not self.rerank_base_url or not self.rerank_api_key:
                raise ValueError("API reranker requires RERANK_BASE_URL and RERANK_API_KEY")
            return APIReranker(self.rerank_api_key, self.rerank_base_url, self.rerank_model)
        raise ValueError("reranker must be 'none', 'lexical', or 'api'")

    # ---- Admin Lifecycle ----
    def create_tenant(
        self,
        tenant_id: str,
        *,
        display_name: str | None = None,
        quota_docs: int = 10000,
        quota_chunks: int = 100000,
        rate_limit_rpm: int = 120,
        description: str = "",
        actor_id: str = "platform_admin",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        tenant = self.control_plane.create_tenant(
            tenant_id,
            display_name=display_name,
            quota_docs=quota_docs,
            quota_chunks=quota_chunks,
            rate_limit_rpm=rate_limit_rpm,
            description=description,
        )
        self.pool.get_node(tenant_id, quota_docs=quota_docs)
        self.control_plane.append_audit(
            actor_type="admin",
            actor_id=actor_id,
            tenant_id=tenant_id,
            action="tenant.create",
            resource_type="tenant",
            resource_id=tenant_id,
            request_id=request_id,
            detail={"quota_docs": quota_docs, "quota_chunks": quota_chunks, "rate_limit_rpm": rate_limit_rpm},
        )
        return tenant

    def update_tenant(
        self,
        tenant_id: str,
        *,
        display_name: str | None = None,
        status: str | None = None,
        quota_docs: int | None = None,
        quota_chunks: int | None = None,
        rate_limit_rpm: int | None = None,
        description: str | None = None,
        actor_id: str = "platform_admin",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        updated = self.control_plane.update_tenant(
            tenant_id,
            display_name=display_name,
            status=status,
            quota_docs=quota_docs,
            quota_chunks=quota_chunks,
            rate_limit_rpm=rate_limit_rpm,
            description=description,
        )
        if quota_docs is not None:
            self.pool.sync_quota(tenant_id, quota_docs)
        self.control_plane.append_audit(
            actor_type="admin",
            actor_id=actor_id,
            tenant_id=tenant_id,
            action="tenant.update",
            resource_type="tenant",
            resource_id=tenant_id,
            request_id=request_id,
            detail={"status": updated["status"], "quota_docs": updated["quota_docs"], "rate_limit_rpm": updated["rate_limit_rpm"]},
        )
        return updated

    def delete_tenant(self, tenant_id: str, *, actor_id: str = "platform_admin", request_id: str | None = None) -> bool:
        self.control_plane.get_tenant(tenant_id)
        self.pool.shred_tenant(tenant_id)
        deleted = self.control_plane.delete_tenant_record(tenant_id)
        self.control_plane.append_audit(
            actor_type="admin",
            actor_id=actor_id,
            tenant_id=tenant_id,
            action="tenant.shred",
            resource_type="tenant",
            resource_id=tenant_id,
            request_id=request_id,
            detail={"physical_shredded": True},
        )
        return deleted

    def create_backup(self, tenant_id: str, *, actor_id: str = "platform_admin", request_id: str | None = None) -> dict[str, Any]:
        self.control_plane.get_tenant(tenant_id)
        bkp = self.pool.create_backup(tenant_id)
        rec = self.control_plane.record_backup(
            bkp["backup_id"],
            tenant_id,
            bkp["file_path"],
            bkp["size_bytes"],
            bkp["sha256"],
            bkp["integrity_ok"],
            bkp["doc_count"],
        )
        self.control_plane.append_audit(
            actor_type="admin",
            actor_id=actor_id,
            tenant_id=tenant_id,
            action="backup.create",
            resource_type="backup",
            resource_id=bkp["backup_id"],
            request_id=request_id,
            detail={"sha256": bkp["sha256"], "doc_count": bkp["doc_count"], "size_bytes": bkp["size_bytes"]},
        )
        return rec

    def restore_backup(
        self, tenant_id: str, backup_id: str, *, actor_id: str = "platform_admin", request_id: str | None = None
    ) -> dict[str, Any]:
        self.control_plane.get_tenant(tenant_id)
        rec = self.control_plane.get_backup(backup_id)
        if rec["tenant_id"] != tenant_id:
            raise NotFoundError(f"backup {backup_id!r} does not belong to tenant {tenant_id!r}")
        stats = self.pool.restore_backup(tenant_id, rec["file_path"], expected_sha256=rec["sha256"])
        self.control_plane.append_audit(
            actor_type="admin",
            actor_id=actor_id,
            tenant_id=tenant_id,
            action="backup.restore",
            resource_type="backup",
            resource_id=backup_id,
            request_id=request_id,
            detail={"sha256": rec["sha256"], "restored_docs": stats["docs"]},
        )
        return stats

    def get_tenant_node(self, tenant_id: str) -> TenantNode:
        t = self.control_plane.get_tenant(tenant_id)
        if t["status"] != "active":
            raise ForbiddenError(f"tenant {tenant_id!r} is suspended", error_code="tenant_suspended")
        return self.pool.get_node(tenant_id, quota_docs=t["quota_docs"])

    # ---- Tenant Operations ----
    def _enforce_quotas(self, tenant_id: str, node: TenantNode, doc_id: str | None, estimated_chunks: int) -> None:
        t = self.control_plane.get_tenant(tenant_id)
        st = node.stats()
        is_existing = bool(doc_id and node.get_document_detail(doc_id) is not None)
        if not is_existing and st["docs"] >= t["quota_docs"]:
            raise ForbiddenError("document quota exceeded", error_code="quota_exceeded")
        if not is_existing and st["chunks"] + estimated_chunks > t["quota_chunks"]:
            raise ForbiddenError("chunk quota exceeded", error_code="quota_exceeded")

    def ingest_text(
        self,
        tenant_id: str,
        text: str,
        *,
        doc_id: str | None = None,
        metadata: dict | None = None,
        source: str | None = None,
        format: str = "text",
        max_chars: int = 1000,
        overlap: int = 100,
        actor_id: str = "api_key",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        node = self.get_tenant_node(tenant_id)
        est_chunks = max(1, (len(text) // max(1, max_chars - overlap)) + 1)
        self._enforce_quotas(tenant_id, node, doc_id, est_chunks)
        res = node.ingest_doc(
            text,
            doc_id=doc_id,
            metadata=metadata,
            source=source,
            format=format,
            max_chars=max_chars,
            overlap=overlap,
        )
        self.metrics.inc_ingestion()
        self.control_plane.append_audit(
            actor_type="tenant_key",
            actor_id=actor_id,
            tenant_id=tenant_id,
            action="doc.ingest",
            resource_type="document",
            resource_id=res["doc_id"],
            request_id=request_id,
            detail={"chunks": res["chunks"], "unchanged": res["unchanged"], "source": source},
        )
        return res

    def ingest_file_bytes(
        self,
        tenant_id: str,
        filename: str,
        raw: bytes,
        *,
        doc_id: str | None = None,
        metadata: dict | None = None,
        acl: dict | None = None,
        max_chars: int = 1000,
        overlap: int = 100,
        async_job: bool = False,
        actor_id: str = "api_key",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        parsed = parse_document_bytes(filename, raw)
        merged_meta = dict(metadata or {})
        merged_meta.update(parsed["extracted_metadata"])
        if acl is not None:
            merged_meta["_acl"] = acl
        target_doc_id = doc_id or Path(filename).stem[:64]

        if not async_job:
            res = self.ingest_text(
                tenant_id,
                parsed["text"],
                doc_id=target_doc_id,
                metadata=merged_meta,
                source=filename,
                format=parsed["format"],
                max_chars=max_chars,
                overlap=overlap,
                actor_id=actor_id,
                request_id=request_id,
            )
            return {"mode": "sync", "document": res, "parsed_metadata": parsed["extracted_metadata"]}

        def run_job(progress_cb) -> dict[str, Any]:
            progress_cb(0, 0, 30)
            res = self.ingest_text(
                tenant_id,
                parsed["text"],
                doc_id=target_doc_id,
                metadata=merged_meta,
                source=filename,
                format=parsed["format"],
                max_chars=max_chars,
                overlap=overlap,
                actor_id=actor_id,
                request_id=request_id,
            )
            progress_cb(1, res["chunks"], 95)
            return {
                "doc_id": res["doc_id"],
                "chunks": res["chunks"],
                "total_chunks": res["chunks"],
                "processed_docs": 1,
                "unchanged": res["unchanged"],
            }

        job = self.jobs.submit(
            tenant_id=tenant_id,
            job_type="file_ingest",
            filename=filename,
            file_format=parsed["extracted_metadata"]["file_format"],
            total_docs=1,
            fn=run_job,
        )
        return {"mode": "async", "job": job}

    def search(
        self,
        tenant_id: str,
        query: str,
        *,
        k: int = 10,
        fusion: str = "rrf",
        lexical_weight: float | None = None,
        filters: dict[str, Any] | None = None,
        principal: dict[str, Any] | None = None,
        reranker_mode: str = "lexical",
        context_window: int = 0,
        dedupe_parent: bool = False,
        check_abstention: bool = True,
        min_vector_score: float = 0.28,
        explain: bool = False,
        actor_id: str = "api_key",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        node = self.get_tenant_node(tenant_id)
        reranker = self.resolve_reranker(reranker_mode)
        res = enterprise_search(
            node,
            query,
            k=k,
            fusion=fusion,
            lexical_weight=lexical_weight,
            filters=filters,
            principal=principal,
            reranker=reranker,
            context_window=context_window,
            dedupe_parent=dedupe_parent,
            check_abstention=check_abstention,
            min_vector_score=min_vector_score,
            explain=explain,
        )
        self.metrics.inc_search(abstained=not res["evidence_sufficient"])
        self.control_plane.append_audit(
            actor_type="tenant_key",
            actor_id=actor_id,
            tenant_id=tenant_id,
            action="doc.search",
            resource_type="search",
            resource_id=None,
            request_id=request_id,
            latency_ms=res["latency_ms"],
            detail={
                "hits": len(res["hits"]),
                "fusion": fusion,
                "reranker": reranker.name,
                "evidence_sufficient": res["evidence_sufficient"],
            },
        )
        return res

    def run_eval_suite(
        self,
        tenant_id: str,
        *,
        suite: str = "golden",
        k: int = 3,
        custom_cases: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run reproducible live retrieval comparison across 5 modes (Vector, FTS, RRF, Score, Rerank)."""
        import tempfile

        modes = [
            {"id": "vector", "label": "Vector Only", "fusion": "score", "lexical_weight": 0.0, "reranker": "none"},
            {"id": "fts", "label": "FTS5 Trigram", "fusion": "fts_only", "lexical_weight": None, "reranker": "none"},
            {"id": "hybrid_rrf", "label": "Hybrid RRF", "fusion": "rrf", "lexical_weight": None, "reranker": "none"},
            {"id": "hybrid_score", "label": "Hybrid Score", "fusion": "score", "lexical_weight": 0.2, "reranker": "none"},
            {"id": "hybrid_rerank", "label": "Hybrid + Rerank", "fusion": "rrf", "lexical_weight": None, "reranker": "lexical"},
        ]

        if suite == "tenant_live":
            node = self.get_tenant_node(tenant_id)
            cases = custom_cases or []
            if not cases:
                # Auto-generate representative probes from current tenant docs
                docs = node.list_docs(limit=20)
                for idx, d in enumerate(docs[:6]):
                    snippet = d["text"].strip().splitlines()[0].lstrip("# ").strip()[:40]
                    if snippet:
                        cases.append({"id": f"live-{idx+1}", "query": snippet, "expected": [d["doc_id"]]})
            return self._evaluate_node(node, cases, modes, k=k, dataset_label=f"tenant:{tenant_id}")

        # Golden benchmark in ephemeral physical node
        golden_path = Path(__file__).resolve().parents[2] / "eval" / "golden.jsonl"
        if not golden_path.is_file():
            raise NotFoundError("eval/golden.jsonl not found")
        docs_data, queries_data = [], []
        for line in golden_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") == "doc":
                docs_data.append(row)
            elif row.get("type") == "query":
                if suite in ("all", "golden") or row.get("suite") == suite:
                    queries_data.append(row)

        with tempfile.TemporaryDirectory() as tmp:
            temp_pool = TenantPool(tmp, self.embedder, embedding_identity=self.embedding_identity)
            try:
                eval_node = temp_pool.get_node("eval-bench", quota_docs=5000)
                for d in docs_data:
                    eval_node.index_doc(d["text"], doc_id=d["doc_id"], metadata=d.get("metadata"))
                cases = [
                    {"id": q.get("id", f"q{i+1}"), "query": q["query"], "expected": q["expected"], "suite": q.get("suite", "pipeline")}
                    for i, q in enumerate(queries_data)
                ]
                return self._evaluate_node(eval_node, cases, modes, k=k, dataset_label=f"golden:{suite}")
            finally:
                temp_pool.close_all()

    def _evaluate_node(
        self,
        node: TenantNode,
        cases: list[dict[str, Any]],
        modes: list[dict[str, Any]],
        *,
        k: int,
        dataset_label: str,
    ) -> dict[str, Any]:
        from tenantrag.search import build_fts_query

        k = max(1, min(int(k), 20))
        summary: dict[str, dict[str, float]] = {}
        per_query: list[dict[str, Any]] = []

        for q in cases:
            per_query.append({"id": q["id"], "query": q["query"], "expected": q["expected"], "suite": q.get("suite", "custom"), "results": {}})

        for m in modes:
            recalls, mrrs, lats = [], [], []
            for idx, q in enumerate(cases):
                t0 = time.perf_counter()
                if m["fusion"] == "fts_only":
                    fts_q = build_fts_query(q["query"])
                    ranked_ids: list[str] = []
                    if fts_q:
                        with node.read_connection() as con:
                            rows = con.execute(
                                "SELECT doc_id FROM docs_fts WHERE docs_fts MATCH ? AND tenant_id=? ORDER BY bm25(docs_fts) LIMIT ?",
                                (fts_q, node.tenant_id, k),
                            ).fetchall()
                            ranked_ids = [r[0] for r in rows]
                else:
                    res = enterprise_search(
                        node,
                        q["query"],
                        k=k,
                        fusion=m["fusion"],
                        lexical_weight=m["lexical_weight"],
                        reranker=self.resolve_reranker(m["reranker"]),
                        check_abstention=False,
                    )
                    ranked_ids = []
                    for h in res["hits"]:
                        pid = (h.get("metadata") or {}).get("_chunk", {}).get("parent_id") or h["doc_id"]
                        if pid not in ranked_ids:
                            ranked_ids.append(pid)
                lat = (time.perf_counter() - t0) * 1000.0
                lats.append(lat)
                expected = set(q["expected"])
                recall = len(expected & set(ranked_ids[:k])) / max(1, len(expected))
                mrr = next((1.0 / rank for rank, did in enumerate(ranked_ids[:k], 1) if did in expected), 0.0)
                recalls.append(recall)
                mrrs.append(mrr)
                per_query[idx]["results"][m["id"]] = {
                    "ranked": ranked_ids[:k],
                    "recall": round(recall, 3),
                    "mrr": round(mrr, 3),
                }
            n = max(1, len(cases))
            summary[m["id"]] = {
                "label": m["label"],
                "recall_at_k": round(sum(recalls) / n, 4),
                "mrr_at_k": round(sum(mrrs) / n, 4),
                "avg_latency_ms": round(sum(lats) / n, 2),
            }

        return {
            "dataset": dataset_label,
            "k": k,
            "query_count": len(cases),
            "embedder": getattr(self.embedder, "embedding_identity", {"model": "unknown"}),
            "summary": summary,
            "queries": per_query,
        }

    def platform_overview(self) -> dict[str, Any]:
        tenants = self.control_plane.list_tenants()
        tenant_rows = []
        total_docs = 0
        total_chunks = 0
        total_bytes = 0
        for t in tenants:
            tid = t["tenant_id"]
            db_file = self.pool.tenants_dir / tid / "index.db"
            if db_file.exists():
                node = self.pool.get_node(tid, quota_docs=t["quota_docs"])
                st = node.stats()
            else:
                st = {"docs": 0, "chunks": 0, "db_bytes": 0, "model_identity": None}
            total_docs += st["docs"]
            total_chunks += st["chunks"]
            total_bytes += st["db_bytes"]
            tenant_rows.append({**t, "stats": st})
        return {
            "tenants": tenant_rows,
            "totals": {
                "tenants": len(tenants),
                "active_tenants": sum(1 for t in tenants if t["status"] == "active"),
                "docs": total_docs,
                "chunks": total_chunks,
                "storage_bytes": total_bytes,
                "jobs_recent": len(self.control_plane.list_jobs(limit=20)),
                "backups": len(self.control_plane.list_backups()),
            },
            "metrics": self.metrics.summary(),
            "embedder": getattr(self.embedder, "embedding_identity", {}),
            "llm_configured": bool(self.chat.llm_base_url and self.chat.llm_api_key),
        }

    def close(self) -> None:
        self.jobs.shutdown(wait=True)
        self.pool.close_all()
        self.control_plane.close()
