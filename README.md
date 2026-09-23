<div align="center">

# tenant-rag

**Multi-tenant hybrid retrieval for personal AI apps.**
Vector + keyword search, per-tenant isolation, one SQLite file, zero ops.

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![SQLite + FTS5](https://img.shields.io/badge/storage-SQLite%20%2B%20FTS5-green)](https://www.sqlite.org/)
[![Zero dependencies](https://img.shields.io/badge/deps-zero_(stdlib_only)-orange)](#)
[![Tests](https://img.shields.io/badge/tests-58_passing-brightgreen)](#testing)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

[中文文档](README.zh-CN.md)

</div>

---

## What is this?

When your app needs to *"search each user's own private data"*, plug this in. It's a lightweight retrieval backend for indie developers' personal AI apps — **not** another Elasticsearch.

One-liner: hybrid retrieval (semantic vectors + FTS5 keyword/grep) with tenant-grade isolation, backed by a single SQLite file.

## Why this isn't reinventing the wheel

ES / OpenSearch / Qdrant / pgvector are wheels — but they're **heavy infrastructure**: deploy it, operate it, and multi-tenant isolation is DIY (row-level security, payload filters; miss one spot and data leaks across tenants). This project's cut is **"small and multi-tenant"**:

- **Personal-scale corpora** (a few thousand docs per tenant) — brute-force vector search is plenty; ANN is overkill
- **Isolation out of the box** — API key *is* the tenant; cross-tenant leakage is locked down by tests
- **Eval-gated retrieval** — change the tokenizer or swap the embedding model, check the numbers before shipping

If it grows past this cut, it collides with the wheels — see [Non-goals](#non-goals).

## Features

| | |
|---|---|
| 🔀 **Hybrid retrieval** | Vector (cosine) + FTS5 trigram, fused with RRF — no training, the most robust production baseline |
| 🔒 **Tenant isolation** | API key = tenant identity; every query is tenant-scoped; leakage covered by dedicated tests |
| 🌏 **CJK-friendly** | FTS5 trigram + sliding-trigram query rewriting; short CJK terms fall back to the vector channel — that's the point of hybrid |
| 📊 **Eval gate** | Golden set + `run_eval.py --min-recall 0.8` — tune parameters against numbers, not vibes |
| 🧾 **Quotas & metering** | Per-tenant doc quotas, daily request metering, token-bucket rate limiting (60/min default) |
| 🌐 **HTTP API** | Stdlib-only server, zero dependencies: `POST /v1/index`, `POST /v1/search`, `DELETE /v1/docs/<id>`, `GET /v1/docs?limit=&offset=`, `GET /v1/usage`, `GET /v1/health` |
| 🖥️ **Admin UI** | Upload docs (auto-embedded server-side), browse/delete, hybrid search with channel badges |
| 🐶 **Dogfooded** | Ships with a subscription-manager agent demo built on top of it |

## Quickstart

```python
from tenantrag import TenantRAG
from tenantrag.embed import FakeEmbedder  # offline; swap in OpenAIEmbedder for prod

rag = TenantRAG("app.db", embedder=FakeEmbedder())
rag.register("user-123", api_key="sk-xxx", quota_docs=5000)

rag.index("sk-xxx", "Netflix renews at $15.49/month", doc_id="bill-1")
hits = rag.search("sk-xxx", "how much is Netflix")
# [{'doc_id': 'bill-1', 'text': ..., 'score': ..., 'matched': ['vector', 'fts']}]
```

Production embeddings via any OpenAI-compatible API (DeepSeek, SiliconFlow, local vLLM…):

```python
from tenantrag.embed import OpenAIEmbedder
emb = OpenAIEmbedder(api_key="...", base_url="https://api.siliconflow.cn/v1",
                     model="BAAI/bge-m3")
```

Or over HTTP:

```bash
PYTHONPATH=. python3 -m tenantrag.server --db tenantrag.db --port 8000

curl -H "Authorization: Bearer sk-xxx" -H "Content-Type: application/json" \
  -d '{"query":"how much is Netflix","k":5}' \
  http://127.0.0.1:8000/v1/search
```

Error mapping: bad key → `401`, quota/isolation → `403`, rate limit → `429` + `Retry-After`.

## Architecture

```
search(query)
 ├─ query → embedding        → vector_search   (cosine; non-positive filtered)
 ├─ query → build_fts_query  → fts_search      (FTS5 trigram, tenant-scoped)
 └─ RRF fusion → fetch_docs → hits (each tagged: matched = ["vector"] / ["fts"] / both)
```

Three things that are easy to get wrong (marked in code):

1. **Tenant-scoped FTS** — the FTS5 table doesn't store `tenant_id`; every full-text query must be tenant-filtered or one `MATCH` leaks across tenants. Locked by `test_isolation.py`.
2. **CJK tokenization** — `unicode61` indexes "腾讯视频" as one token, so single-character queries miss; trigram + query-side sliding trigrams fix it. Terms too short for trigram fall back to the vector channel.
3. **User input into MATCH** — quotes / `AND` / parens trigger FTS5 syntax errors; `build_fts_query` extracts tokens and escapes them instead.

## Admin UI (`admin/`)

A stdlib-only web UI that reverse-proxies the HTTP API (same origin, no CORS issues, never touches SQLite directly):

```bash
# 1. start the API
PYTHONPATH=. python3 -m tenantrag.server --db tenantrag.db --port 8000
# 2. start the admin UI
python3 admin/admin_server.py --api http://127.0.0.1:8000 --port 8002
# 3. open http://127.0.0.1:8002/ and paste a tenant API key
```

- Tenant overview (docs/quota, today's requests, rate limit)
- Upload docs — paste text or drop `.txt`/`.md` files; embedding happens server-side
- Browse (paginated) & delete docs
- Hybrid search with per-hit channel badges (vector / keyword) and RRF scores

> The key lives only in page JS memory. The admin UI has no auth of its own and binds `127.0.0.1` — it's a local ops tool, don't expose it publicly. Tenant creation stays in the Python API (`TenantRAG.register`).

## Evaluation

```bash
python3 eval/run_eval.py --k 3
# | hybrid | 0.929 | 1.000 |   (recall@3, MRR)
# | vector | 0.929 | 1.000 |
# | fts    | 0.571 | 0.571 |
```

Honest disclaimer: the default `FakeEmbedder` is a **lexical** hash-bag embedder, so vector ≈ hybrid here — these numbers validate the pipeline, not semantic quality. With real embeddings, expect hybrid > vector (paraphrased queries) > fts. The eval set's value is as a regression gate, not an absolute score.

## Design notes

1. **Why hybrid** — pure vectors fumble order numbers / amounts / names; pure keywords fumble paraphrases. RRF needs no training and is the steadiest production baseline.
2. **Isolation, and its trade-offs** — MVP: single DB, logical isolation (every SQL carries `tenant_id`) + tests. Upgrade path: one SQLite file per tenant (physical isolation), at the cost of file count and backup complexity.
3. **The CJK trap** — measured `unicode61` vs trigram on real queries ("腾讯视频" indexed whole, single chars unfindable); query-side sliding trigrams.
4. **Eval-driven** — golden set first, tune against it, gate in CI. Same philosophy as guardrail layers.
5. **Dogfood-driven schema** — the subscription-manager demo immediately showed `doc_id` as a global primary key was wrong ("bill-txvideo" in two tenants is *natural*). Became a `(tenant_id, doc_id)` composite key with automatic migration for old DBs — the value of having a user before having a schema.

## Engineering log — 7 bug-hunt rounds, 26 issues (2026-09-23)

Every suspected bug was reproduced before fixing; every fix got a regression test. Tests grew **24 → 58**, all green; eval scores unchanged.

| Round | Focus | Found & fixed |
|---|---|---|
| 1 | API correctness | 8 — unnormalized dot product posing as cosine; missing input validation on index; uncaught HTTP exceptions → connection resets; global HTTP lock wrapping embedding network calls; unbounded rate-limit buckets; negative-`k` slicing; bare SQLite errors on duplicate tenant; hollow answers for priceless subscriptions |
| 2 | Robustness | 5 — duplicate `api_key` → raw `IntegrityError`; `api_key=None` → `AttributeError` instead of `AuthError`; empty string → FTS5 syntax error; non-int `k` → `TypeError`; `list_docs` without offset → silent undercount on large tenants |
| 3 | Concurrency & SQLite | 2 — `InterfaceError` under multithreaded load (`check_same_thread=False` doesn't make concurrent `execute()` safe; all connection access now under RLock); cross-process quota oversell (quota=30 wrote 31; `BEGIN IMMEDIATE` makes check-then-write atomic) |
| 4 | FTS casing | 0 — verified FTS5 trigram is ASCII case-insensitive; locked with regression tests |
| 5 | Security & DoS | 2 — slowloris (no socket timeout; added `timeout = 10`); weak API keys rejected at registration (`<8` chars refused — the key *is* the identity) |
| 6 | Consistency & ops | 3 — missing rollback in multi-statement upsert; delete-miss holding a write lock without commit; migration half-failure silently orphaning data (now wrapped in an explicit transaction, verified fail → old schema intact → retry succeeds) |
| 7 | API & demo consistency | 4 — Python `usage()` missing `requests_today`; currency filter silently ignored by totals in the demo; EUR not detected; unbounded composer input now truncated at 500 chars |

Regression assets live in `tests/test_robustness.py`.

## Ops notes

- **Backup** — in WAL mode the DB is three files (`*.db` + `*.db-wal` + `*.db-shm`); copying only `*.db` loses recent commits. Run `PRAGMA wal_checkpoint(TRUNCATE)` first, or use `VACUUM INTO 'backup.db'` / `sqlite3 db .dump`.
- **Usage table growth** — `store.purge_usage(keep_days=90)`; run it monthly via cron.
- **Key policy** — registration enforces ≥ 8 chars; the key *is* the tenant identity (leak = tenant takeover). Use random strings and HTTPS (or bind `127.0.0.1` only).
- **`k` limits** — HTTP `/v1/search` clamps `k` to 100; the Python API's `k` is bounded by the candidate pool (`vec_k=50` + `fts_k=50`).

## Demo: subscription-manager agent (`demo/`)

A zero-model-cost agent dogfooding the retrieval layer: intent detection → data fetch → templated answers, every answer citing `sources: [doc_id]`. Lookup goes through hybrid retrieval; aggregations (total / most expensive / list) page through `list_docs` — the real division of labor in RAG apps. Two tenants asking "how much is 腾讯视频" get different answers: isolation you can see.

## Non-goals

- Distributed / high-concurrency serving — personal-scale doesn't need it
- Large-scale ANN — brute force is fine for thousands of docs per tenant; upgrade path is `sqlite-vss`
- Learned rerankers — RRF is enough, interface left open

## License

MIT — see [LICENSE](LICENSE).
