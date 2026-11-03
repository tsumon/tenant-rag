<div align="center">

# tenant-rag

**Your users' documents. Their own search results. One SQLite database.**

Small, tenant-scoped hybrid retrieval for personal AI applications.

[![CI](https://github.com/tsumon/tenant-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/tsumon/tenant-rag/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Runtime dependencies: zero](https://img.shields.io/badge/runtime_dependencies-zero-green)
[![MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

[中文](README.zh-CN.md) · [Release checklist](docs/RELEASING.md) · [Evaluation](#evaluation)

</div>

## What you get

- **Hybrid search:** cosine vectors + SQLite FTS5 trigram keywords, combined with reciprocal rank fusion (RRF).
- **Logical tenant isolation:** API keys resolve to tenants; documents, searches, deletion, quotas and usage are tenant-scoped. Different tenants can reuse a document ID.
- **Small operational footprint:** a SQLite database, Python standard library runtime, optional OpenAI-compatible embeddings.
- **Usable interfaces:** Python library, bearer-auth HTTP API, and a local browser administration tool.
- **Regression coverage:** isolation, migration rollback, concurrent quota enforcement, malformed requests and deterministic retrieval evaluation.

This is a **retrieval component**, not a complete knowledge-base product or hosted SaaS. It does not generate answers, parse PDFs, chunk long documents, manage user accounts, enforce document ACLs inside a tenant, or provide distributed storage. The included subscription agent demonstrates retrieval with templated answers, not an LLM agent platform.

## Install from source

Requires **Python 3.10+** and SQLite compiled with **FTS5 and the trigram tokenizer** (SQLite 3.34+). The core has no third-party runtime dependencies. Building packages uses setuptools.

```bash
git clone https://github.com/tsumon/tenant-rag.git
cd tenant-rag
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

This documents a source install, not an already-published PyPI release. Check SQLite support:

```bash
python -c "import sqlite3; sqlite3.connect(':memory:').execute(\"CREATE VIRTUAL TABLE probe USING fts5(text, tokenize='trigram')\")"
```

## Five-minute local demo

Create one random tenant key and initialize **the same database** the server will use. Keep this shell open for the curl commands; tenant registration is a one-time operation and rejects duplicates.

```bash
export TENANT_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python - <<'PY'
import os
from tenantrag import TenantRAG
with TenantRAG('tenantrag.db') as rag:
    rag.register('demo-user', os.environ['TENANT_KEY'], quota_docs=5000)
    rag.index(os.environ['TENANT_KEY'], 'Netflix renews at $15.49/month', doc_id='bill-1')
    print(rag.search(os.environ['TENANT_KEY'], 'Netflix price'))
PY

# Run in another terminal, from this directory with the venv activated:
tenant-rag --db tenantrag.db --port 8000
```

Back in the shell holding TENANT_KEY:

```bash
curl -sS http://127.0.0.1:8000/v1/search \
  -H "Authorization: Bearer $TENANT_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"Netflix price","k":5}'
```

Default embeddings are deterministic **lexical hash vectors**, not semantic embeddings. No model downloads, provider calls or API cost are needed for this demo.

### Local admin UI

```bash
tenant-rag-admin --api http://127.0.0.1:8000 --port 8002
```

Open http://127.0.0.1:8002 and paste the generated tenant key. Browse, upload text/Markdown, search and delete documents. The key stays in page memory, not browser storage. Tenant creation remains a trusted Python operation. Both servers bind to loopback by default; the admin UI is a local tool, **not a public admin console**. The wheel includes its HTML asset.

## Python API

```python
from tenantrag import TenantRAG
from tenantrag.embed import FakeEmbedder

with TenantRAG('example.db', embedder=FakeEmbedder()) as rag:
    rag.register('alice', 'example-only-key-change-me')
    rag.index('example-only-key-change-me', 'Order ABC-2026 shipped', doc_id='order')
    hits = rag.search('example-only-key-change-me', 'ABC-2026', k=5)
    docs = rag.list_docs('example-only-key-change-me', limit=100, offset=0)
    usage = rag.usage('example-only-key-change-me')
    deleted = rag.delete('example-only-key-change-me', 'order')
```

Use randomly generated secrets in real applications. Indexing an existing ID replaces its text, metadata and embedding. Pagination is explicit. Python calls do not increment HTTP request metering or use the HTTP rate limiter.

## HTTP API

Except health, endpoints require `Authorization: Bearer <tenant-key>`.

| Method | Endpoint | Payload / result |
|---|---|---|
| POST | `/v1/index` | `text`, optional `doc_id`, `metadata` → document ID |
| POST | `/v1/search` | `query`, optional `k` → ranked hits with `matched` channels |
| GET | `/v1/docs?limit=100&offset=0` | paginated documents |
| DELETE | `/v1/docs/<url-encoded-id>` | `deleted` boolean |
| GET | `/v1/usage` | document quota, today's requests, rate policy |
| GET | `/v1/health` | unauthenticated process liveness, not provider readiness |

Errors: malformed input `400`, invalid key `401`, quota `403`, rate limit `429` with `Retry-After`, unexpected backend/provider failure `500`. JSON bodies are limited to 10 MiB. Search `k` clamps to 1–100, list `limit` to 1–1000. HTTP requests by valid tenants are counted, including failed/rate-limited requests and the usage request itself. Daily usage uses the server's local date. Default rate limit is 60/minute per tenant and per server process.

## Real embeddings: explicit opt-in

The API server uses a real provider only when `--embed-base-url` or `EMBED_BASE_URL` is set:

```bash
export EMBED_BASE_URL='http://127.0.0.1:8080/v1'
export EMBED_MODEL='your-served-embedding-model'
export EMBED_API_KEY='local-demo'  # Dummy bearer value ONLY for a local no-auth endpoint.
# For authenticated endpoints, securely set the actual provider key instead.
tenant-rag --db semantic.db
```

For Python, supply `OpenAIEmbedder(api_key=..., base_url=..., model=...)` to `TenantRAG`. Use a provider's documented embeddings-compatible endpoint, HTTPS for remote services, and an appropriate data-processing policy. Documents and queries are transmitted to that provider and may incur charges. Never commit credentials.

**Choose one embedding model per database. Changing the provider, model or dimensions requires reindexing all documents.** Model identity is not persisted or automatically migrated. Different dimensions are skipped by vector search, and same-dimension model changes are not detectable. A separate new database is the safest migration target.

## Evaluation

```bash
python -m unittest discover -v
python eval/run_eval.py --k 3 --min-recall 0.8 --min-mrr 0.7
python eval/run_eval.py --json --output eval-report.json
python demo/run_demo.py
```

The default gate checks the **pipeline regression suite** using fake lexical embeddings. A separate bilingual semantic challenge suite reports diagnostic results; synthetic fixtures are not a production quality benchmark. Reports identify the embedder and suite. Recall@k and reciprocal rank at the retrieved cutoff compare hybrid, vector and keyword channels.

To deliberately evaluate an actual embeddings endpoint against the included synthetic texts:

```bash
python eval/run_eval.py --embedder openai --allow-provider-requests \
  --base-url http://127.0.0.1:8080/v1 --model your-served-embedding-model \
  --gate-suite semantic --output semantic-report.json
```

Set EMBED_API_KEY (use a non-secret dummy value only for local endpoints that ignore authentication). This opt-in makes network requests and can incur cost; inspect the dataset and endpoint first. **No real-model quality result is claimed by this repository's offline tests.** Hybrid is not guaranteed to outperform vectors on every dataset. Choose quality thresholds after measuring representative, consented data.

## How retrieval works

```text
API key → tenant ID
query ┬→ embedder → tenant-filtered cosine candidates ┐
      └→ safe trigram FTS query → tenant-filtered hits ├→ RRF → tenant-scoped documents
                                                      ┘
```

FTS stores `tenant_id` as an unindexed column and filters it explicitly. Query text is tokenized rather than interpolated as FTS syntax. Short CJK terms cannot match the trigram channel and rely on vectors. The vector channel scans each tenant's corpus; start with personal-scale data and measure latency rather than assuming a capacity guarantee. RRF scores are ranking scores, not probabilities or confidence estimates.

## Operating boundaries

- **Not internet-hardened:** stdlib threaded HTTP is intended for local/trusted deployments. Use TLS, gateway authentication controls, request/concurrency limits and monitoring before any public deployment. In-process rate limits are not global across workers.
- **Shared storage:** logical isolation is not physical isolation or encryption. Protect database files, host access and backups. Keys are stored as SHA-256 hashes; a leaked bearer key grants tenant access. There is no built-in key rotation/revocation workflow.
- **Backups:** use SQLite's online backup API or `VACUUM INTO`, then test restoration. Copying only a live `.db` file is unsafe in WAL mode, even after a checkpoint if writers resume. Stop all writers before filesystem-level copies.
- **Maintenance:** `rag.store.purge_usage(keep_days=90)` removes older metering rows. Legacy global document-ID schemas migrate transactionally on open; take a verified backup first.
- **Not yet covered:** real-provider availability/quality, production load, full browser interaction QA, distributed deployment and adversarial security audit. See the [release checklist](docs/RELEASING.md) before publishing.

## License

[MIT](LICENSE).
