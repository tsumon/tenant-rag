# Changelog

## 0.6.0 (2026-11-18)

- **Enterprise Dual-Mode Architecture**: Run `pip install -e ".[enterprise]"` and `tenant-rag-enterprise` to launch a FastAPI production server with physical per-tenant SQLite WAL isolation (`TenantPool`), read-only concurrency pools, and `VectorCache`, while keeping the core `tenantrag` SDK 100% zero-dependency and backward compatible.
- **Control Plane IAM & Tamper-Evident Audit**: Provision, suspend, or physically shred tenants; issue scoped API keys (`read`, `write`, `chat`, `eval`, `tenant_admin`) with TTLs, zero-downtime rotation, and revocation; verify audit integrity via SHA-256 hash-chaining (`/api/v1/admin/audit/verify`).
- **Pre-Retrieval ACL, Metadata DSL, Reranking & Grounded SSE Chat**: Enforce `_acl` visibility/roles and structured metadata filters (`$eq`, `$gte`, `$in`, `$contains`, etc.) before candidate selection; refine ranks with `LexicalCrossReranker` or `/rerank` HTTP endpoints; expand sibling chunk context (`context_window`); abstain on unsupported queries (`min_vector_score`); stream grounded `[n]` character-offset citations via `/api/v1/chat/stream`.
- **Multi-Format Ingestion, Hot Backups & Observability**: Parse `.md`, `.txt`, `.html`, `.csv`/`.tsv`, `.json`/`.jsonl`, `.docx`, and `.pdf` uploads synchronously or via `AsyncJobManager`; create and restore integrity-checked SQLite hot backups; monitor live latency and counters at `/api/v1/ready` and Prometheus `/metrics`.
- **React 19 Dual-Perspective Workbench & Docker**: Manage tenants, keys, backups, audit logs, chunk offset highlighting, retrieval waterfall debugging, citation chat, online benchmarks, and dual-tenant isolation checks from the bundled UI or `docker compose up -d --build`.

## 0.5.1 (2026-11-05)

- Add a bilingual, reproducible local real-model trial with synthetic data,
  checked-in evidence and offline evidence verification.
- Pin the trial to the official 0.5.0 wheel for historical reproducibility;
  independently smoke-test the 0.5.1 wheel and source distribution.
- Package trial source, documentation and evidence without weights, wheels,
  environments or databases. Add dedicated real-model CI and release gates.
- No core runtime, API or database schema changes. Retrieval still returns
  ranked fragments for unanswerable questions; this is not answer generation.

## 0.5.0 (2026-11-05)

- Add explicitly selected cosine-score/keyword-rank fusion in SDK, HTTP and
  evaluation tools, preserving the default RRF behavior and tenant checks.
- Add trusted local safetensors evaluation with source fingerprints, strict
  same-input report comparisons and independent synthetic retrieval evidence.
- Reject incomplete/corrupt comparison metadata, invalid hash encodings and
  oversized numeric metrics without producing a misleading comparison.
- Document long-input truncation, numeric-reasoning limits, shared FTS statistics
  and the difference between core and optional model-stack compatibility.
- Update the optional model dependency baseline after reviewing current
  advisories. Do not redistribute weights/environments as part of this package.
- No database schema change or required 0.4.0 reindex for fusion-mode selection.

## 0.4.0

Model identity validation, explicit legacy adoption, atomic reindex, deterministic
text/Markdown chunks, source offsets, parent lifecycle and tenant quotas.

## 0.3.0

Installable standard-library package, HTTP/admin entry points, reproducible
pipeline checks, supported-Python CI and distribution smoke tests.
