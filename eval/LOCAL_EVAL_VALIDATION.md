# Local evaluation validation, 2026-11-05

Historical record from before the first actual model run. Subsequent
fixed-snapshot measurements and their limitations are described in
[the fusion study](../docs/FUSION.md); the initial checks below are preserved.

This is a development validation record, not a release or a real-model quality
result. Base: tenant-rag v0.4.0, commit
`6c858a39468e6e4f101f32aed6dc01fb84d5f058`.
Validation runtime: Python 3.12.14, SQLite 3.53.1, setuptools 84.2.1.

## What was verified

- Full standard-library test suite: **180 tests passed**, including 25 new local
  adapter/report comparison tests. Model-loader tests use mocks; fixture weight
  bytes are not actual pretrained weights and are never used as quality results.
- Existing offline pipeline gate (`k=3`, recall >= 0.8, MRR >= 0.7): passed.
  Hybrid Recall@3 = **0.96875**, MRR@3 = **1.0**, on 16 synthetic pipeline queries.
- Two same-environment default JSON runs were byte-identical. Comparing them
  produced zero deltas and 30 unchanged-metric queries.
- Versioned operations corpus checksum validated. Its 2 pipeline queries had
  hybrid Recall@3/MRR@3 = **1.0/1.0**. The 11 semantic queries' fake-vector
  diagnostic was **0.272727/0.151515**; this is not semantic-model evidence.
- Local semantic-only operations dry run: **23 embedding texts**, **0 planned
  provider requests**, no dependency import/model load and no quality result.
- Core wheel and source distribution built through the installed setuptools
  PEP 517 backend without network. Installed-wheel HTTP/admin round trip and
  v0.4 ingestion/isolation/reindex/cancellation/legacy-adoption smoke scripts
  passed outside the checkout with no optional model dependencies.
- `git diff --check` passed. Existing corpora and their labels were not changed.

## What remains unverified

**No real embedding model was executed.** The execution environment had no
torch, transformers, sentence-transformers, pretrained model cache, or local
model weights. Its permitted network destinations did not include the official
package/model download hosts. No package/model download, paid provider request,
private customer corpus, or private reminder data was used. Missing model
access was not worked around through another host or service.

The optional Sentence Transformers 3.4.1 interface was checked against upstream
source and tested with mocks, not installed end to end. Exact compatibility of
the resolved torch/transformers dependencies, tokenizer/weights, CPU inference,
and language/retrieval quality still needs a real snapshot run. The standard
`python -m build` frontend was unavailable here; direct `setuptools.build_meta`
was used instead. This does not verify the CI Python-version matrix.

## Next reproducible measurement

1. Use an allowed environment, install `.[local-eval]`, and save an environment
   lockfile. Acquire a licensed official, immutable model snapshot covering the
   corpus languages. Review the model's terms, provenance and required prompts.
2. Use a standalone, safetensors-only root Transformer + Pooling (+ optional
   Normalize) model. Adapter/PEFT, custom module code, custom tokenizer file
   overrides and instruction/prompt-dependent models are intentionally outside
   this adapter's supported scope.
3. Follow the local commands in [README.md](README.md), with offline environment
   flags, identical k/corpus/selection, and thresholds chosen before measuring.
4. Save both full reports and the comparison. Review multilingual, negation,
   hard-negative and multi-relevant failures. Report actual measured scores,
   including any hybrid regressions; do not replace them with fake-vector scores.

The bundled public synthetic cases are small development fixtures, not an
independent holdout or proof of production retrieval quality. Nothing in this
record authorizes or records a deployment, package publication or merge.
