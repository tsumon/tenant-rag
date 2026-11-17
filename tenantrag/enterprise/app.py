"""Enterprise FastAPI production server: IAM, rate limiting, SSE chat, admin console & v1 compat."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any
import uuid

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tenantrag.identity import EmbeddingIdentityError
from tenantrag.service import IngestionConflictError, QuotaError

from .demo_seed import ensure_demo_seeded
from .engine import EnterpriseTenantRAG
from .errors import EnterpriseError, ForbiddenError, RateLimitedError, UnauthorizedError


STATIC_DIR = Path(__file__).resolve().parent / "static"


class CreateTenantBody(BaseModel):
    tenant_id: str
    display_name: str | None = None
    quota_docs: int = Field(default=10000, ge=0)
    quota_chunks: int = Field(default=100000, ge=0)
    rate_limit_rpm: int = Field(default=120, ge=1)
    description: str = ""


class UpdateTenantBody(BaseModel):
    display_name: str | None = None
    status: str | None = None
    quota_docs: int | None = Field(default=None, ge=0)
    quota_chunks: int | None = Field(default=None, ge=0)
    rate_limit_rpm: int | None = Field(default=None, ge=1)
    description: str | None = None


class IssueKeyBody(BaseModel):
    name: str = "api-key"
    scopes: list[str] = Field(default_factory=lambda: ["read", "write", "chat", "eval"])
    expires_in_days: float | None = Field(default=None, gt=0)


class RotateKeyBody(BaseModel):
    grace_seconds: int = Field(default=3600, ge=0)
    name: str | None = None
    expires_in_days: float | None = Field(default=None, gt=0)


class IngestBody(BaseModel):
    text: str
    doc_id: str | None = None
    metadata: dict[str, Any] | None = None
    acl: dict[str, Any] | None = None
    source: str | None = None
    format: str = "text"
    max_chars: int = Field(default=1000, ge=1)
    overlap: int = Field(default=100, ge=0)


class SearchBody(BaseModel):
    query: str
    k: int = Field(default=10, ge=1, le=100)
    fusion: str = "rrf"
    lexical_weight: float | None = None
    filters: dict[str, Any] | None = None
    principal: dict[str, Any] | None = None
    reranker: str = "lexical"
    context_window: int = Field(default=1, ge=0, le=5)
    dedupe_parent: bool = False
    check_abstention: bool = True
    min_vector_score: float = 0.28
    explain: bool = True


class ChatBody(SearchBody):
    llm: dict[str, Any] | None = None


class EvalRunBody(BaseModel):
    suite: str = "golden"
    k: int = Field(default=3, ge=1, le=20)
    cases: list[dict[str, Any]] | None = None


class LegacyIndexBody(BaseModel):
    text: str
    doc_id: str | None = None
    metadata: dict[str, Any] | None = None


class LegacyIngestBatchBody(BaseModel):
    documents: list[dict[str, Any]]
    max_chars: int = 1000
    overlap: int = 100


class LegacySearchBody(BaseModel):
    query: str
    k: int = 10
    fusion: str = "rrf"
    lexical_weight: float | None = None


def create_app(
    engine: EnterpriseTenantRAG | None = None,
    *,
    admin_token: str | None = None,
    demo_mode: bool = False,
    seed_demo: bool = False,
    data_dir: str | Path = "./enterprise_data",
) -> FastAPI:
    own_engine = engine is None
    if engine is None:
        engine = EnterpriseTenantRAG(data_dir=data_dir)

    resolved_admin = (admin_token or os.environ.get("TENANT_RAG_ADMIN_TOKEN") or "").strip() or None
    if demo_mode and not resolved_admin:
        resolved_admin = "tr_admin_demo_token_2026"

    if seed_demo:
        ensure_demo_seeded(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if own_engine:
                engine.close()

    app = FastAPI(
        title="Tenant RAG Enterprise Platform",
        version="0.6.0",
        description="Enterprise multi-tenant hybrid RAG with physical SQLite isolation, IAM, ACL, rerank, grounded citations, and observability.",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.admin_token = resolved_admin
    app.state.demo_mode = demo_mode

    # ---- Error Handlers ----
    @app.exception_handler(EnterpriseError)
    async def _enterprise_err(request: Request, exc: EnterpriseError):
        content: dict[str, Any] = {"error": exc.error_code, "hint": str(exc)}
        headers = getattr(exc, "headers", None)
        if isinstance(exc, RateLimitedError):
            content["retry_after"] = exc.retry_after
        return JSONResponse(status_code=exc.status_code, content=content, headers=headers)

    @app.exception_handler(ValueError)
    async def _value_err(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "hint": str(exc)},
        )

    @app.exception_handler(QuotaError)
    async def _quota_err(request: Request, exc: QuotaError):
        return JSONResponse(
            status_code=403,
            content={"error": "quota_exceeded", "hint": str(exc)},
        )

    @app.exception_handler(IngestionConflictError)
    async def _ingest_conflict_err(request: Request, exc: IngestionConflictError):
        return JSONResponse(
            status_code=409,
            content={"error": "ingestion_conflict", "hint": str(exc)},
        )

    @app.exception_handler(EmbeddingIdentityError)
    async def _identity_err(request: Request, exc: EmbeddingIdentityError):
        return JSONResponse(
            status_code=409,
            content={"error": "embedding_identity_conflict", "hint": str(exc)},
        )

    # ---- Request ID & Prometheus Metrics Middleware ----
    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = req_id
        t0 = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        route_tpl = request.scope.get("route").path if request.scope.get("route") else request.url.path
        engine.metrics.observe_http(request.method, route_tpl, response.status_code, elapsed_ms)
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
        rl = getattr(request.state, "rate_limit", None)
        if rl:
            response.headers["X-RateLimit-Limit"] = str(rl["limit"])
            response.headers["X-RateLimit-Remaining"] = str(rl["remaining"])
        return response

    # ---- Auth Dependencies ----
    def _extract_bearer(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise UnauthorizedError("missing Authorization: Bearer <api_key>")
        token = authorization[7:].strip()
        if not token:
            raise UnauthorizedError("empty bearer token")
        return token

    def auth_scope(required_scope: str | None = None):
        def _dep(
            request: Request,
            authorization: str | None = Header(default=None),
        ) -> dict[str, Any]:
            raw_key = _extract_bearer(authorization)
            ctx = engine.control_plane.authenticate_key(raw_key, required_scope=required_scope)
            ok, rl_info = engine.limiter.allow(ctx["tenant_id"], ctx["rate_limit_rpm"])
            if not ok:
                engine.control_plane.append_audit(
                    actor_type="tenant_key",
                    actor_id=ctx["key_prefix"],
                    tenant_id=ctx["tenant_id"],
                    action="rate_limit.rejected",
                    status="rate_limited",
                    request_id=getattr(request.state, "request_id", None),
                )
                raise RateLimitedError(
                    f"rate limit exceeded; retry after {rl_info['retry_after']}s",
                    retry_after=rl_info["retry_after"],
                    headers={
                        "Retry-After": str(rl_info["retry_after"]),
                        "X-RateLimit-Limit": str(rl_info["limit"]),
                        "X-RateLimit-Remaining": "0",
                    },
                )
            request.state.rate_limit = rl_info
            ctx["rate_limit"] = rl_info
            ctx["request_id"] = getattr(request.state, "request_id", None)
            return ctx

        return _dep

    def require_admin(
        request: Request,
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
        authorization: str | None = Header(default=None),
    ) -> str:
        if not app.state.admin_token:
            raise ForbiddenError("platform admin API disabled (configure TENANT_RAG_ADMIN_TOKEN)", error_code="admin_disabled")
        candidate = x_admin_token
        if not candidate and authorization and authorization.startswith("Bearer "):
            candidate = authorization[7:].strip()
        if not candidate or not secrets.compare_digest(candidate, app.state.admin_token):
            raise UnauthorizedError("invalid platform admin token")
        return "platform_admin"

    # ---- Health, Readiness & Observability ----
    @app.get("/v1/health")
    @app.get("/api/v1/health")
    def health():
        return {"ok": True, "version": "0.6.0", "demo_mode": demo_mode}

    @app.get("/api/v1/ready")
    def ready():
        try:
            tenants = engine.control_plane.list_tenants()
            return {
                "ready": True,
                "control_plane": "ok",
                "tenants": len(tenants),
                "embedder": getattr(engine.embedder, "embedding_identity", {}),
            }
        except Exception as exc:
            return JSONResponse(status_code=503, content={"ready": False, "error": str(exc)})

    @app.get("/metrics")
    def prometheus_metrics():
        return PlainTextResponse(engine.metrics.prometheus_text(), media_type="text/plain; version=0.0.4; charset=utf-8")

    # ---- Platform SuperAdmin Endpoints ----
    @app.get("/api/v1/admin/demo/bootstrap")
    def admin_demo_bootstrap():
        if not app.state.demo_mode:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        seeded = ensure_demo_seeded(engine)
        return {
            "demo_mode": True,
            "admin_token": app.state.admin_token,
            **seeded,
        }

    @app.get("/api/v1/admin/overview")
    def admin_overview(_admin: str = Depends(require_admin)):
        return engine.platform_overview()

    @app.get("/api/v1/admin/tenants")
    def admin_list_tenants(_admin: str = Depends(require_admin)):
        return {"tenants": engine.platform_overview()["tenants"]}

    @app.post("/api/v1/admin/tenants")
    def admin_create_tenant(body: CreateTenantBody, request: Request, admin_id: str = Depends(require_admin)):
        tenant = engine.create_tenant(
            body.tenant_id,
            display_name=body.display_name,
            quota_docs=body.quota_docs,
            quota_chunks=body.quota_chunks,
            rate_limit_rpm=body.rate_limit_rpm,
            description=body.description,
            actor_id=admin_id,
            request_id=request.state.request_id,
        )
        return {"tenant": tenant}

    @app.patch("/api/v1/admin/tenants/{tenant_id}")
    def admin_update_tenant(tenant_id: str, body: UpdateTenantBody, request: Request, admin_id: str = Depends(require_admin)):
        tenant = engine.update_tenant(
            tenant_id,
            display_name=body.display_name,
            status=body.status,
            quota_docs=body.quota_docs,
            quota_chunks=body.quota_chunks,
            rate_limit_rpm=body.rate_limit_rpm,
            description=body.description,
            actor_id=admin_id,
            request_id=request.state.request_id,
        )
        return {"tenant": tenant}

    @app.delete("/api/v1/admin/tenants/{tenant_id}")
    def admin_delete_tenant(tenant_id: str, request: Request, admin_id: str = Depends(require_admin)):
        deleted = engine.delete_tenant(tenant_id, actor_id=admin_id, request_id=request.state.request_id)
        return {"deleted": deleted, "tenant_id": tenant_id}

    @app.get("/api/v1/admin/keys")
    def admin_list_keys(tenant_id: str | None = None, _admin: str = Depends(require_admin)):
        return {"keys": engine.control_plane.list_keys(tenant_id=tenant_id)}

    @app.post("/api/v1/admin/tenants/{tenant_id}/keys")
    def admin_issue_key(tenant_id: str, body: IssueKeyBody, request: Request, admin_id: str = Depends(require_admin)):
        issued = engine.control_plane.issue_key(
            tenant_id,
            name=body.name,
            scopes=body.scopes,
            expires_in_days=body.expires_in_days,
        )
        engine.control_plane.append_audit(
            actor_type="admin",
            actor_id=admin_id,
            tenant_id=tenant_id,
            action="key.issue",
            resource_type="api_key",
            resource_id=issued["key_id"],
            request_id=request.state.request_id,
            detail={"scopes": issued["scopes"], "name": issued["name"]},
        )
        return {"key": issued}

    @app.post("/api/v1/admin/keys/{key_id}/rotate")
    def admin_rotate_key(key_id: str, body: RotateKeyBody, request: Request, admin_id: str = Depends(require_admin)):
        res = engine.control_plane.rotate_key(
            key_id,
            grace_seconds=body.grace_seconds,
            name=body.name,
            expires_in_days=body.expires_in_days,
        )
        engine.control_plane.append_audit(
            actor_type="admin",
            actor_id=admin_id,
            tenant_id=res["new_key"]["tenant_id"],
            action="key.rotate",
            resource_type="api_key",
            resource_id=key_id,
            request_id=request.state.request_id,
            detail={"new_key_id": res["new_key"]["key_id"], "grace_seconds": body.grace_seconds},
        )
        return res

    @app.post("/api/v1/admin/keys/{key_id}/revoke")
    def admin_revoke_key(key_id: str, request: Request, admin_id: str = Depends(require_admin)):
        revoked = engine.control_plane.revoke_key(key_id)
        engine.control_plane.append_audit(
            actor_type="admin",
            actor_id=admin_id,
            tenant_id=revoked["tenant_id"],
            action="key.revoke",
            resource_type="api_key",
            resource_id=key_id,
            request_id=request.state.request_id,
        )
        return {"key": revoked}

    @app.get("/api/v1/admin/backups")
    def admin_list_backups(tenant_id: str | None = None, _admin: str = Depends(require_admin)):
        return {"backups": engine.control_plane.list_backups(tenant_id=tenant_id)}

    @app.post("/api/v1/admin/tenants/{tenant_id}/backups")
    def admin_create_backup(tenant_id: str, request: Request, admin_id: str = Depends(require_admin)):
        bkp = engine.create_backup(tenant_id, actor_id=admin_id, request_id=request.state.request_id)
        return {"backup": bkp}

    @app.post("/api/v1/admin/tenants/{tenant_id}/backups/{backup_id}/restore")
    def admin_restore_backup(tenant_id: str, backup_id: str, request: Request, admin_id: str = Depends(require_admin)):
        stats = engine.restore_backup(tenant_id, backup_id, actor_id=admin_id, request_id=request.state.request_id)
        return {"restored": True, "stats": stats}

    @app.get("/api/v1/admin/audit")
    def admin_query_audit(
        tenant_id: str | None = None,
        action: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        _admin: str = Depends(require_admin),
    ):
        logs = engine.control_plane.query_audit(
            tenant_id=tenant_id, action=action, status=status, limit=limit, offset=offset
        )
        return {"logs": logs}

    @app.get("/api/v1/admin/audit/verify")
    def admin_verify_audit(_admin: str = Depends(require_admin)):
        return engine.control_plane.verify_audit_chain()

    # ---- Tenant Workspace API (/api/v1/*) ----
    @app.get("/api/v1/whoami")
    def tenant_whoami(ctx: dict = Depends(auth_scope(None))):
        node = engine.get_tenant_node(ctx["tenant_id"])
        st = node.stats()
        return {
            **ctx,
            "stats": st,
            "llm_configured": bool(engine.chat.llm_base_url and engine.chat.llm_api_key),
        }

    @app.get("/api/v1/docs")
    def tenant_list_docs(limit: int = 100, offset: int = 0, ctx: dict = Depends(auth_scope("read"))):
        node = engine.get_tenant_node(ctx["tenant_id"])
        docs = node.list_docs(limit=limit, offset=offset)
        return {"docs": docs, "limit": limit, "offset": offset, "stats": node.stats()}

    @app.get("/api/v1/docs/{doc_id}")
    def tenant_get_doc(doc_id: str, ctx: dict = Depends(auth_scope("read"))):
        node = engine.get_tenant_node(ctx["tenant_id"])
        detail = node.get_document_detail(doc_id)
        if detail is None:
            raise HTTPException(status_code=404, detail={"error": "not_found", "hint": f"document {doc_id!r} not found"})
        return detail

    @app.delete("/api/v1/docs/{doc_id}")
    def tenant_delete_doc(doc_id: str, ctx: dict = Depends(auth_scope("write"))):
        node = engine.get_tenant_node(ctx["tenant_id"])
        deleted = node.delete_doc(doc_id)
        engine.control_plane.append_audit(
            actor_type="tenant_key",
            actor_id=ctx["key_prefix"],
            tenant_id=ctx["tenant_id"],
            action="doc.delete",
            resource_type="document",
            resource_id=doc_id,
            request_id=ctx["request_id"],
            detail={"deleted": deleted},
        )
        return {"deleted": deleted, "doc_id": doc_id}

    @app.post("/api/v1/ingest")
    def tenant_ingest(body: IngestBody, ctx: dict = Depends(auth_scope("write"))):
        meta = dict(body.metadata or {})
        if body.acl is not None:
            meta["_acl"] = body.acl
        return engine.ingest_text(
            ctx["tenant_id"],
            body.text,
            doc_id=body.doc_id,
            metadata=meta,
            source=body.source,
            format=body.format,
            max_chars=body.max_chars,
            overlap=body.overlap,
            actor_id=ctx["key_prefix"],
            request_id=ctx["request_id"],
        )

    @app.post("/api/v1/ingest/files")
    async def tenant_ingest_file(
        file: UploadFile = File(...),
        doc_id: str | None = Form(default=None),
        metadata_json: str = Form(default="{}"),
        acl_json: str = Form(default=""),
        max_chars: int = Form(default=800),
        overlap: int = Form(default=100),
        async_job: bool = Form(default=True),
        ctx: dict = Depends(auth_scope("write")),
    ):
        raw = await file.read()
        try:
            meta = json.loads(metadata_json) if metadata_json.strip() else {}
            if not isinstance(meta, dict):
                raise ValueError("metadata_json must be a JSON object")
        except json.JSONDecodeError as exc:
            raise ValueError("invalid metadata_json") from exc
        acl = None
        if acl_json.strip():
            try:
                acl = json.loads(acl_json)
                if not isinstance(acl, dict):
                    raise ValueError("acl_json must be a JSON object")
            except json.JSONDecodeError as exc:
                raise ValueError("invalid acl_json") from exc

        return engine.ingest_file_bytes(
            ctx["tenant_id"],
            file.filename or "upload.txt",
            raw,
            doc_id=doc_id.strip() if doc_id and doc_id.strip() else None,
            metadata=meta,
            acl=acl,
            max_chars=max(50, int(max_chars)),
            overlap=max(0, int(overlap)),
            async_job=async_job,
            actor_id=ctx["key_prefix"],
            request_id=ctx["request_id"],
        )

    @app.get("/api/v1/jobs")
    def tenant_list_jobs(limit: int = 30, ctx: dict = Depends(auth_scope("read"))):
        return {"jobs": engine.control_plane.list_jobs(tenant_id=ctx["tenant_id"], limit=limit)}

    @app.get("/api/v1/jobs/{job_id}")
    def tenant_get_job(job_id: str, ctx: dict = Depends(auth_scope("read"))):
        return engine.control_plane.get_job(job_id, tenant_id=ctx["tenant_id"])

    @app.post("/api/v1/search")
    def tenant_search(body: SearchBody, ctx: dict = Depends(auth_scope("read"))):
        return engine.search(
            ctx["tenant_id"],
            body.query,
            k=body.k,
            fusion=body.fusion,
            lexical_weight=body.lexical_weight,
            filters=body.filters,
            principal=body.principal,
            reranker_mode=body.reranker,
            context_window=body.context_window,
            dedupe_parent=body.dedupe_parent,
            check_abstention=body.check_abstention,
            min_vector_score=body.min_vector_score,
            explain=body.explain,
            actor_id=ctx["key_prefix"],
            request_id=ctx["request_id"],
        )

    @app.post("/api/v1/chat")
    def tenant_chat(body: ChatBody, ctx: dict = Depends(auth_scope("chat"))):
        search_res = engine.search(
            ctx["tenant_id"],
            body.query,
            k=body.k,
            fusion=body.fusion,
            lexical_weight=body.lexical_weight,
            filters=body.filters,
            principal=body.principal,
            reranker_mode=body.reranker,
            context_window=body.context_window,
            dedupe_parent=body.dedupe_parent,
            check_abstention=body.check_abstention,
            min_vector_score=body.min_vector_score,
            explain=body.explain,
            actor_id=ctx["key_prefix"],
            request_id=ctx["request_id"],
        )
        return engine.chat.answer(search_res, body.query, llm_override=body.llm)

    @app.post("/api/v1/chat/stream")
    def tenant_chat_stream(body: ChatBody, ctx: dict = Depends(auth_scope("chat"))):
        search_res = engine.search(
            ctx["tenant_id"],
            body.query,
            k=body.k,
            fusion=body.fusion,
            lexical_weight=body.lexical_weight,
            filters=body.filters,
            principal=body.principal,
            reranker_mode=body.reranker,
            context_window=body.context_window,
            dedupe_parent=body.dedupe_parent,
            check_abstention=body.check_abstention,
            min_vector_score=body.min_vector_score,
            explain=body.explain,
            actor_id=ctx["key_prefix"],
            request_id=ctx["request_id"],
        )

        def event_stream():
            for event, payload in engine.chat.stream_answer(search_res, body.query, llm_override=body.llm):
                yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/api/v1/eval/run")
    def tenant_eval_run(body: EvalRunBody, ctx: dict = Depends(auth_scope("eval"))):
        return engine.run_eval_suite(
            ctx["tenant_id"],
            suite=body.suite,
            k=body.k,
            custom_cases=body.cases,
        )

    # ---- Backward-Compatible Classic /v1/* Routes ----
    @app.post("/v1/index")
    def v1_index(body: LegacyIndexBody, ctx: dict = Depends(auth_scope("write"))):
        node = engine.get_tenant_node(ctx["tenant_id"])
        engine._enforce_quotas(ctx["tenant_id"], node, body.doc_id, 1)
        doc_id = node.index_doc(body.text, doc_id=body.doc_id, metadata=body.metadata)
        engine.metrics.inc_ingestion()
        return {"doc_id": doc_id}

    @app.post("/v1/ingest")
    def v1_ingest(body: IngestBody, ctx: dict = Depends(auth_scope("write"))):
        return engine.ingest_text(
            ctx["tenant_id"],
            body.text,
            doc_id=body.doc_id,
            metadata=body.metadata,
            source=body.source,
            format=body.format,
            max_chars=body.max_chars,
            overlap=body.overlap,
            actor_id=ctx["key_prefix"],
            request_id=ctx["request_id"],
        )

    @app.post("/v1/ingest/batch")
    def v1_ingest_batch(body: LegacyIngestBatchBody, ctx: dict = Depends(auth_scope("write"))):
        node = engine.get_tenant_node(ctx["tenant_id"])
        res = node.ingest_batch(body.documents, max_chars=body.max_chars, overlap=body.overlap)
        engine.metrics.inc_ingestion()
        return {"documents": res}

    @app.post("/v1/search")
    def v1_search(body: LegacySearchBody, ctx: dict = Depends(auth_scope("read"))):
        res = engine.search(
            ctx["tenant_id"],
            body.query,
            k=max(1, min(int(body.k), 100)),
            fusion=body.fusion,
            lexical_weight=body.lexical_weight,
            reranker_mode="none",
            check_abstention=False,
            explain=False,
            actor_id=ctx["key_prefix"],
            request_id=ctx["request_id"],
        )
        legacy_hits = [
            {
                "doc_id": h["doc_id"],
                "text": h["text"],
                "metadata": h["metadata"],
                "score": h["score"],
                "matched": h["matched"],
            }
            for h in res["hits"]
        ]
        return {"hits": legacy_hits}

    @app.get("/v1/docs")
    def v1_list_docs(limit: int = 100, offset: int = 0, ctx: dict = Depends(auth_scope("read"))):
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        node = engine.get_tenant_node(ctx["tenant_id"])
        docs = [{"doc_id": d["doc_id"], "text": d["text"], "metadata": d["metadata"]} for d in node.list_docs(limit, offset)]
        return {"docs": docs, "limit": limit, "offset": offset}

    @app.delete("/v1/docs/{doc_id}")
    def v1_delete_doc(doc_id: str, ctx: dict = Depends(auth_scope("write"))):
        node = engine.get_tenant_node(ctx["tenant_id"])
        return {"deleted": node.delete_doc(doc_id)}

    @app.get("/v1/usage")
    def v1_usage(ctx: dict = Depends(auth_scope("read"))):
        node = engine.get_tenant_node(ctx["tenant_id"])
        st = node.stats()
        return {
            "tenant_id": ctx["tenant_id"],
            "docs": st["docs"],
            "quota_docs": ctx["quota_docs"],
            "requests_today": node.rag.store.request_count(ctx["tenant_id"]),
            "rate_limit": {"capacity": ctx["rate_limit_rpm"], "per_seconds": 60},
        }

    # ---- Frontend SPA Static Mount ----
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    @app.get("/{full_path:path}")
    def serve_spa(full_path: str = ""):
        if full_path.startswith(("api/", "v1/", "metrics")):
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        if full_path:
            candidate = (STATIC_DIR / full_path).resolve()
            if candidate.is_relative_to(STATIC_DIR.resolve()) and candidate.is_file():
                return FileResponse(str(candidate))
        index_html = STATIC_DIR / "index.html"
        if index_html.is_file():
            return FileResponse(str(index_html))
        return JSONResponse(
            status_code=200,
            content={
                "service": "tenant-rag-enterprise",
                "message": "Frontend build not found in tenantrag/enterprise/static. Run npm run build inside web/.",
            },
        )

    return app
