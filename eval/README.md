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

## Free local-model evaluation (optional, source checkout)

`--embedder local` runs a trusted, **already downloaded** Sentence Transformers
snapshot on CPU. It does not use provider credentials or paid APIs. The default
installation still has zero runtime dependencies; install the optional model
stack only in an environment where package downloads are allowed:

```sh
python -m pip install '.[local-eval]'
python eval/run_eval.py --embedder local --gate-suite semantic --dry-run
```

The dry run works without optional dependencies or model weights. It validates
the corpus and reports 54 embedding texts and zero provider requests for the
default corpus; it does not prove that the local model can load.

Obtain an appropriately licensed model from its official source separately.
Pin its immutable upstream revision and keep that acquisition record alongside
your run. A bilingual corpus needs a model whose documented language coverage
includes English and Chinese. This adapter supports a root Transformer followed
by Pooling and an optional Normalize module, **safetensors weights only**, and no
custom Python model code. A full repository clone containing additional `.bin`
weights is rejected: prepare a dedicated snapshot containing the necessary
configuration/tokenizer files and safetensors weights, not legacy pickle files.
Model IDs are never resolved or downloaded by this runner. Adapter/PEFT models,
external tokenizer paths and shards outside the snapshot are rejected. The
adapter explicitly constructs known modules with offline parameters instead of
using the generic model loader's fallback paths. Optional Sentence Transformers
is pinned to 3.4.1, whose loader API was inspected; dependency integration still
requires an actual model run. Only `max_seq_length` and `do_lower_case` are
accepted in the saved Transformer module configuration.

```sh
# MODEL_DIR is an existing trusted model directory containing modules.json.
# Offline flags add a process-wide guard to the loader's local_files_only=True.
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
python eval/run_eval.py --embedder local --model-path "$MODEL_DIR" \
  --gate-suite semantic --output local-report.json

# The second versioned corpus uses the same local model and no provider calls.
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
python eval/run_eval.py --embedder local --model-path "$MODEL_DIR" \
  --golden eval/operations-v1.jsonl --manifest eval/operations-v1.manifest.json \
  --gate-suite semantic --output operations-local-report.json
```

Reports record a SHA-256 over local relative filenames and file bytes (including
weights/tokenizer/configuration), dependency versions, CPU execution, normalized
vectors and model sequence limit. The snapshot is hashed before and after
loading; keep it unchanged for the entire run. Snapshot symlink files are
followed; directory symlinks are rejected so loaded subdirectories cannot escape
the fingerprint. Fingerprints identify bytes, not model provenance or trustworthiness.
No absolute model path is written to reports. `--model` and `--revision` describe
only the OpenAI backend; local identity comes from the file fingerprint.

The adapter uses an explicit empty prompt for both documents and queries.
**Do not use models requiring asymmetric query/document instructions** (for
example, required `query:`/`passage:` prefixes) with this adapter. Text beyond
the model's recorded sequence limit may be truncated by the model; this is not
a chunking evaluation. Numerical results may differ across dependency versions,
hardware or CPU math implementations even with identical weights. Save the
report and an environment lockfile for exact run reconstruction.

The initial adapter tests use mocked outputs and are not model-quality evidence.
A subsequent actual fixed-snapshot run and fusion experiments are recorded in
[the fusion study](../docs/FUSION.md). The original
[validation record](LOCAL_EVAL_VALIDATION.md) remains as historical context.
Default zero thresholds are only a smoke check, not a quality target.

## Compare runs and inspect regressions

```sh
# Both runs must use the same checkout, complete corpus and query selection.
python eval/run_eval.py --json --output offline-report.json
python eval/compare_reports.py offline-report.json local-report.json \
  --output comparison.json
```

The comparison refuses different corpus/selection hashes, k, candidate limits,
retrieval settings or implementation fingerprints. It prints per-suite/channel
metric changes plus the changed queries' before/after rankings. Deltas are
candidate minus baseline; unchanged metrics do not imply identical rankings.
Python/SQLite differences are flagged, and both backend runtime configurations
are included for review. Comparing lexical and pretrained results describes
observations on these fixtures, not a statistically controlled model claim.
Compare pretrained models using the same runtime and review query failures,
especially bilingual and hard-negative cases. Exit 0 means compatible reports,
not that the candidate improved; invalid or incompatible reports exit 2.

For an intentional same-model fusion experiment, use `--fusion score` with an
optional `--lexical-weight` in 0..1, then compare with
`--allow-fusion-change`. This requires identical backend/runtime and full
per-query vector/FTS candidate fingerprints, while allowing only fusion settings
to differ. Legacy RRF remains the default. See the [formula and study
protocol](../docs/FUSION.md); do not select a weight on the dataset claimed as
an unseen test.

## Explicit real-provider evaluation (optional)

This sends fixture documents and selected queries to your configured provider
and may incur charges. Review the endpoint and corpus first. The provider path
requires both flags below; credentials alone never enable network requests.

```sh
# Set EMBED_API_KEY securely in your environment; do not commit credentials.
# OPENAI_API_KEY is also accepted if EMBED_API_KEY is absent.
export EMBED_BASE_URL='https://api.openai.com/v1'
export EMBED_MODEL='text-embedding-3-small'
# Set a real immutable deployment revision if your provider exposes one.
# Otherwise leave EMBED_REVISION unset; do not invent a revision.
# export EMBED_REVISION='your-immutable-deployment-revision'
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

## v0.4 reproducibility and a second challenge corpus

`operations-v1.jsonl` is a separate, versioned synthetic challenge: 12 policy
passages, 2 literal pipeline queries and 11 semantic queries. It covers bilingual
paraphrases, adjacent-policy hard negatives, negation, a multi-relevant question,
model migration and source-offset interpretation. Every query includes its
label rationale. The adjacent `operations-v1.manifest.json` records provenance,
label policy and the exact file SHA-256. No customer data or model-generated
retrieval results were used. This is public development material, **not a hidden
holdout, independently adjudicated labels, or proof of operational correctness**.

Validate the request plan before deliberately allowing any provider calls:

```sh
python3 eval/run_eval.py \
  --golden eval/operations-v1.jsonl \
  --manifest eval/operations-v1.manifest.json \
  --embedder openai --suite semantic --gate-suite semantic --dry-run
```

Dry run requires no key or provider opt-in, constructs no embedder, and prints
only a validated plan. For this selection it plans 23 requests: all 12 documents
plus the 11 semantic queries. It does not write a report via `--output`, estimate
cost, or produce a quality/gate result. Inspect the corpus and your provider's
pricing before replacing `--dry-run` with `--allow-provider-requests` and setting
credentials. There are no automatic retries. To inspect this corpus offline:

```sh
python3 eval/run_eval.py --golden eval/operations-v1.jsonl \
  --manifest eval/operations-v1.manifest.json --json --output operations-offline.json
```

The manifest is optional for custom corpora. When supplied, a hash mismatch is
rejected before constructing any provider client. Do not rewrite the manifest
merely to silence a mismatch: review the changed labels and bump the corpus
version. Original `golden.jsonl` remains unchanged for baseline comparisons.

Schema-v2 reports include both the original file checksum and a checksum of the
parsed documents plus selected queries, Python/SQLite versions, implementation
file hashes, explicit retrieval settings, and per-tag metrics. Tags overlap, so
their query counts must not be summed; small tag groups are diagnostics rather
than statistically reliable model comparisons. Reports contain queries and
relevance labels: treat custom-data reports as private data too. This harness
still indexes each document as a single retrieval unit (`chunking: false`);
chunk ingestion is covered by its own correctness tests, not by a semantic
chunk-quality claim here.

For a meaningful application evaluation, create a separate consented dataset
matching actual document lengths and language mix. Have a domain owner review
relevant IDs and plausible distractors, freeze/version the data before comparing
models, and reserve an unseen holdout. Keep k, candidate limits, corpus and query
selection identical across runs. Pass `--revision` (or `EMBED_REVISION`) to record your provider's immutable
model revision as well as its label; hosted aliases can change. The report
records an endpoint hash rather than the endpoint URL, so keep the exact
endpoint in your private run configuration. A declared revision is not provider
attestation, and the default `unspecified` cannot detect silent alias changes. Compare failures
and tag slices alongside averages. Choose thresholds before the final holdout
run. Offline fixtures and this entry point do **not** establish real-model quality.
