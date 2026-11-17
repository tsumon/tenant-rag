## tenant-rag 0.6.0

Enterprise Dual-Mode release: preserves the 100% dependency-free `tenantrag` core SDK, database schema compatibility, and classic `/v1/*` endpoints while adding the optional `[enterprise]` production server, physical per-tenant SQLite WAL pool, IAM control plane, pre-retrieval ACL/metadata filtering, two-stage reranking, grounded SSE citation chat, and bundled React 19 workbench. Distributed through GitHub Releases, not PyPI.

### What's new in 0.6.0

- **Dual-Mode Compatibility**: Core `dependencies = []` remains unchanged. Classic `TenantRAG`, `Store`, `/v1/*`, `tenant-rag`, and `tenant-rag-admin` pass all existing tests across Python 3.10–3.14.
- **Enterprise Control Plane & Physical Isolation (`tenantrag.enterprise`)**:
  - `ControlPlane`: tenant lifecycle (`active` / `suspended`), dynamic doc/chunk/RPM quotas, scoped API keys (`read`, `write`, `chat`, `eval`, `tenant_admin`), TTL expiry, zero-downtime key rotation, revocation, and SHA-256 hash-chained audit logs (`/api/v1/admin/audit/verify`).
  - `TenantPool`: isolated per-tenant SQLite WAL files (`tenants/<tenant_id>/index.db`), concurrent read-only connection pooling, generation-tracked `VectorCache`, online `sqlite3.backup()` hot snapshots with `PRAGMA integrity_check` + SHA-256 verification, and restore/shred operations.
- **Pre-Retrieval ACL, Metadata DSL, Reranking & Grounded SSE Chat**:
  - Pre-retrieval structured metadata filters (`$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`, `$contains`, `$exists`) and `_acl` role/user filtering before candidate truncation.
  - Two-stage reranking (`LexicalCrossReranker` for CJK short terms and exact codes, plus `APIReranker` for `/rerank` HTTP endpoints), parent-child `context_window` expansion, and evidence-sufficiency abstention gating (`min_vector_score`).
  - Multi-format document parsers (`.md`, `.txt`, `.html`, `.csv`/`.tsv`, `.json`/`.jsonl`, stdlib `.docx` XML, and `.pdf` via `pypdf`) with `AsyncJobManager` queue tracking.
  - `/api/v1/chat` and `/api/v1/chat/stream` SSE grounded synthesis with inline `[n]` character-offset citations (supports OpenAI-compatible LLMs or offline extractive synthesis), plus Prometheus `/metrics` and `/api/v1/ready`.
- **Bundled React 19 + TypeScript Dual-Perspective Workbench & Docker**:
  - Pre-built static console bundled under `tenantrag/enterprise/static/` and served directly by `tenant-rag-enterprise` (`Platform Admin` + `Tenant Workspace`), alongside `Dockerfile` and `docker-compose.yml` in the source distribution.

### Install and verify

Download `tenant_rag-0.6.0-py3-none-any.whl` and `SHA256SUMS` from this release, verify the checksums, and install either mode:

```bash
# Verify downloaded release assets against exact commit SHA
python scripts/verify_release_download.py DOWNLOAD_DIR FULL_COMMIT_SHA 0.6.0

# 1. Embedded zero-dependency core only
python -m pip install tenant_rag-0.6.0-py3-none-any.whl

# 2. Full enterprise production server + bundled React 19 UI
python -m pip install "tenant_rag-0.6.0-py3-none-any.whl[enterprise]"
tenant-rag-enterprise --data-dir ./enterprise_data --port 8080 --demo-mode --seed-demo
```

Requires **Python 3.10+** and SQLite compiled with **FTS5 + trigram tokenizer**.

### Boundaries and verification

The six release assets are the wheel, source distribution, `offline-eval.json`, `evidence-verification.txt`, `SOURCE_COMMIT.txt`, and `SHA256SUMS`. Core tests, offline pipeline gates, bundled React static assets, and isolated wheel/source smoke checks run across Python 3.10–3.14; the optional `[enterprise]` FastAPI HTTP integration tests and live browser UI verification were verified on Python 3.13. Default `FakeEmbedder` produces deterministic lexical hash vectors—connect real embedding/LLM endpoints (`EMBED_BASE_URL`, `LLM_BASE_URL`) and benchmark representative corpora before setting production quality gates.
