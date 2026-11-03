# Retrieval evaluation

This is a **small synthetic regression corpus, not a production benchmark**.
All records are invented fixtures. It currently contains 24 documents and 30
queries, split into two suites:

- **Pipeline (16 queries):** mostly literal names, identifiers, amounts and CJK
  matches. Suitable for offline regressions in indexing, tokenization and fusion.
- **Semantic (14 queries):** paraphrases, English/Chinese cross-language queries,
  negation, access-control wording and hard negatives. FakeEmbedder scores on
  these queries are diagnostics only, not evidence of semantic understanding.

The semantic suite includes refund vs. subscription cancellation and password
recovery vs. suspicious email distinctions. Labels are manually authored and
have not been validated against real users' traffic. A real embedding model may
improve or worsen any channel. **There is no guaranteed hybrid > vector > FTS
ordering.** No real provider evaluation was run for this release.

## Offline regression gate

From the repository root:

```sh
python3 eval/run_eval.py --k 3 --min-recall 0.8 --min-mrr 0.7
python3 eval/run_eval.py --json --output eval-report.json
```

No network requests are made, even if provider environment variables are set.
The gate applies to **pipeline / hybrid** by default. The semantic scores remain
separate and cannot be used as a semantic gate with FakeEmbedder.

Reference offline results for the bundled corpus (`k=3`):

| Suite | Channel | Recall@3 | MRR@3 |
| --- | --- | ---: | ---: |
| Pipeline | Hybrid | 0.969 | 1.000 |
| Pipeline | Vector | 0.969 | 1.000 |
| Pipeline | FTS | 0.812 | 0.812 |
| Semantic (unverified diagnostic) | Hybrid | 0.429 | 0.393 |
| Semantic (unverified diagnostic) | Vector | 0.357 | 0.321 |
| Semantic (unverified diagnostic) | FTS | 0.286 | 0.250 |

These are results of the deterministic hash-bag embedder, **not measurements of
real semantic retrieval quality**. Re-run after any fixture or implementation
change. Reports include the corpus SHA-256, model label, dimension, SQLite
version, per-suite metrics, per-query rankings, relevant IDs and gate outcome.
There are no timestamps or variable wall-clock measurements, so repeated
same-environment offline JSON reports can be compared byte for byte.

## Explicit real-provider evaluation (optional)

This sends fixture documents and selected queries to your configured provider
and may incur charges. Review the endpoint and corpus first. The provider path
requires both flags below; credentials alone never enable network requests.

```sh
# Set EMBED_API_KEY securely in your environment; do not commit credentials.
# OPENAI_API_KEY is also accepted if EMBED_API_KEY is absent.
export EMBED_BASE_URL='https://api.openai.com/v1'
export EMBED_MODEL='text-embedding-3-small'
python3 eval/run_eval.py \
  --embedder openai --allow-provider-requests \
  --gate-suite semantic --min-recall 0.8 --min-mrr 0.7 \
  --output provider-eval.json
```

The sample thresholds are **illustrative targets, not verified performance**.
Choose thresholds from your application's data and acceptable regressions.
`--base-url` and `--model` override the corresponding environment variables.
An unauthenticated local compatible server can use a placeholder such as
`EMBED_API_KEY=local-demo`; it must tolerate the dummy Bearer header. Use HTTPS
for remote providers. Redirects are rejected, so configure the final endpoint
rather than a redirecting URL.

The runner sends one embedding request per document and selected query (54
requests for the full bundled corpus), without automatic retries. Successful
responses cache their dimension, so the report does not trigger a paid probe.
`--suite pipeline` or `--suite semantic` selects queries only; all documents stay
in the corpus as distractors. When selecting only semantic queries, also use
`--gate-suite semantic` and a real provider. Provider failures produce no gate
pass and do not print provider response bodies or credentials.

Provider execution establishes performance on these fixtures only. It does not
validate production traffic, tenant-isolation security, answer generation,
factuality, latency, costs, robustness to adversaries, or statistical significance.
The automated tests mock provider responses and include a local-only redirect
regression; they do not call a hosted model.

## Metric definitions and exit codes

- Recall@k is the fraction of each query's relevant document IDs retrieved in the
  first k results, averaged equally across queries in its suite.
- MRR@k is reciprocal rank of the first relevant hit in the first k results, or
  zero if none appears. It is **truncated MRR**, not full-corpus MRR.
- Each channel considers up to 50 candidates, matching the service. Vector
  results with cosine <= 0 are excluded; hybrid uses the production
  `hybrid_search` function with its default RRF settings.
- Exit **0**: selected hybrid gate passed. With default zero thresholds this is
  a smoke check, not a meaningful quality gate.
- Exit **1**: recall or MRR gate failed; a complete report is still available.
- Exit **2**: invalid input/configuration, provider failure or report-write error.

A fresh temporary SQLite database is created for each run and cleaned up even
when evaluation fails. Report files are saved only when requested.

## Extending the corpus

`golden.jsonl` has one JSON object per line. Document rows have `type: "doc"`,
a unique `doc_id`, non-empty `text`, and optional object `metadata`. Query rows
have `type: "query"`, unique `id`, a `suite` of `pipeline` or `semantic`, non-empty
`query`, unique known document IDs in `expected`, and optional string `tags`.
Legacy queries without a suite default to pipeline.

The loader rejects malformed JSON, duplicate IDs, missing labels and dangling
relevance references before any provider request. Keep obvious lexical checks
in pipeline, and add genuine paraphrases, multilingual cases and plausible
negative documents to semantic. Avoid tuning a model solely to this tiny set.
For deployment, add a separate representative held-out corpus and review the
per-query failures, not only the averages.

## 中文说明

默认评测完全离线。16 条 pipeline 查询用于管线回归，14 条 semantic 查询只展示
词法假向量在语义挑战上的诊断结果，不能据此宣称线上语义质量已验证。
真实接口必须显式传入 `--embedder openai --allow-provider-requests`，可能产生费用，
本次发布未调用真实模型。接入真实模型后仍需用业务留出集验证，混合检索不保证优于
任意单通道。`--min-mrr` 指截断到 k 的 MRR@k。
