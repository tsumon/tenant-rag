# Evaluating cross-language retrieval and fusion

**Score fusion is experimental and remains opt-in. The later public-data study
loses recall to RRF on both English SciFact and the Chinese T2 subset. It does
not justify replacing the default.** See [public results, long-text truncation
and costs](../eval/public-study/README.md).

## Opt-in score fusion

The default remains `fusion="rrf"`, preserving existing API behavior. An
experimental alternative keeps positive cosine similarity and adds a bounded
keyword-ranking bonus:

```text
score = positive cosine similarity + lexical_weight / keyword_rank
```

Keyword ranks start at 1. A document absent from the vector candidate list
starts at zero; a document absent from keyword candidates gets no bonus. The
bonus never exceeds `lexical_weight`. Stable ties retain vector order, then
keyword order. Scores are ranking signals, not probabilities.

```python
hits = rag.search(key, query, k=3, fusion="score", lexical_weight=0.2)
```

The same opt-in is available in POST `/v1/search`:

```json
{"query":"your question", "k":3, "fusion":"score", "lexical_weight":0.2}
```

The score-mode default is 0.2, selected on the two existing development corpora
with the fixed multilingual MiniLM snapshot below. It is **not a universal or
production-validated optimum**. `lexical_weight` must be a finite number in
0..1; booleans and strings are rejected. It is only accepted with score fusion.
Weight zero is the positive-cosine vector-only ablation and skips keyword
retrieval. Invalid settings are rejected before embedding requests.

Both modes retain tenant-filtered candidate queries, model-identity checks,
source/chunk metadata and the 50-candidate limit per channel. Score fusion does
not change indexing, embeddings, stored data or quotas. No reindex is needed
just to compare fusion modes.

## Why compare a different formula?

With the existing RRF constant 60 and at most 50 candidates per channel, a
document present in both lists scores at least `2 / 110`, greater than the
maximum single-channel score `1 / 61`. Thus every intersection hit outranks
every single-channel hit under these defaults, regardless of cosine margins.
This can favor weak same-language keywords over a strong cross-language match.
It is a property of these settings, not a claim that RRF always fails.

A fixed real-model run of the original public synthetic corpora found:

| Development semantic suite | Queries | RRF Recall@3 / MRR@3 | Vector Recall@3 / MRR@3 |
| --- | ---: | ---: | ---: |
| golden | 14 | 1.000 / 0.917 | 0.929 / 0.893 |
| operations-v1 | 11 | 0.636 / 0.439 | 1.000 / 0.803 |

The operations misses involved English questions with relevant Chinese
documents that lacked keyword overlap. Merely reducing RRF keyword weights,
limiting keyword candidates or removing English stopwords did not restore
recall in the tested variants. Turning off keywords also lost a relevant
golden result. These are small development observations, not production
estimates or evidence for one universally best channel.

Score weights 0, 0.02, 0.05, 0.1, 0.2 and 0.4 were compared on the old corpora.
The declared selection rule first excluded settings that worsened either
pipeline suite, then maximized average semantic recall, semantic MRR, and
finally preferred the lower weight. It selected 0.2. Both development semantic
suites then had Recall@3 1.000; MRR@3 was 0.929 and 0.727 respectively. The
literal/pipeline scores stayed at 0.875/0.875 and 1.000/1.000. This is tuning,
**not independent validation**. Numeric/currency query failures were not fixed.

## Historical synthetic model and evaluation procedure

- Official model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
- Upstream revision: `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`.
- Official safetensors SHA-256:
  `eaa086f0ffee582aeb45b36e34cdd1fe2d6de2bef61f8a559a1bbc9bd955917b`.
- Historical run: Sentence Transformers 3.4.1, Transformers 4.48.3, torch 2.6.0+cpu.
- Release baseline: 5.6.0 / 5.13.0 / 2.13.0+cpu, re-evaluated on all three
  old synthetic corpora with unchanged per-query ranks/metrics. Use the new
  [verified environment](../eval/evidence/dependencies/README.md), not the old
  stack with known advisories.
- CPU, normalized vectors, empty prompt, 128-token model limit, k=3.
- All original fixture texts fit within 46 tokens including special tokens.

The actual model was downloaded from its official pinned source and all 12
snapshot files were verified against official Git blob/LFS hashes. Inference
used offline library flags and a Python socket-denial guard. No private data
or paid embedding API was used. A repeated default-corpus real-model report
was byte-identical in the same environment; this does not promise identical
floating-point results across hardware or dependency versions.

To compare the modes from the same checkout and model snapshot:

```sh
python eval/run_eval.py --embedder local --model-path "$MODEL_DIR" \
  --fusion rrf --output rrf.json
python eval/run_eval.py --embedder local --model-path "$MODEL_DIR" \
  --fusion score --lexical-weight 0.2 --output score.json
python eval/compare_reports.py rrf.json score.json --allow-fusion-change
```

Use the offline environment flags and model acquisition guidance in
[the evaluation guide](../eval/README.md). For a fusion comparison, the tool
requires identical model/runtime, corpus selection, implementation, candidate
limits and non-fusion settings. Per-query hashes of the full candidate IDs and
scores must match for both control channels; top-k vector/FTS rankings must
also match. The flag permits only fusion, lexical weight and RRF-constant
changes. Ordinary model comparisons still reject retrieval-setting changes.

## Independent synthetic evaluation protocol

A separate author prepares new multilingual synthetic passages and questions
without seeing tuning outcomes. Another AI reviewer checks every relevance
label before the dataset is frozen. This is **AI-authored and AI-reviewed**,
not human annotation, real customer traffic or a representative industry
benchmark. Dataset bytes and candidate algorithm/settings are frozen before
the implementation author first reads or evaluates the new questions.

The first evaluation must report every language direction, literal/semantic
slice and regression, not only aggregate gains. No weight or algorithm is
retuned on this holdout. Once inspected, the set becomes a public regression
resource and cannot support another claim of unseen evaluation. Any later
strategy change needs a fresh separately labeled holdout.

## First frozen holdout result (2026-11-05)

The candidate code was frozen at commit `938ceffb382ed550e9eeeb9e850a54459aa31e65`
before the strategy author first inspected the new dataset. The dataset has 132
passages and 64 semantic questions; it has **no pipeline suite**. All texts fit
within 47 tokens. No parameters or labels were changed after these results.

| Slice | Questions | RRF Recall / MRR | Score Recall / MRR | Vector Recall / MRR |
| --- | ---: | ---: | ---: | ---: |
| All | 64 | 0.695 / 0.591 | 0.938 / 0.776 | 0.953 / 0.833 |
| English → English | 16 | 0.938 / 0.875 | 1.000 / 0.833 | 1.000 / 0.875 |
| Chinese → Chinese | 16 | 0.938 / 0.813 | 1.000 / 0.813 | 1.000 / 0.844 |
| Chinese → English | 16 | 0.781 / 0.552 | 0.781 / 0.594 | 0.844 / 0.708 |
| English → Chinese | 16 | 0.125 / 0.125 | 0.969 / 0.865 | 0.969 / 0.906 |
| Cross-language | 32 | 0.453 / 0.339 | 0.875 / 0.729 | 0.906 / 0.807 |
| Same-language | 32 | 0.938 / 0.844 | 1.000 / 0.823 | 1.000 / 0.859 |

All metrics are macro averages at k=3. Recall averages the fraction of relevant
documents found per question; four questions have two positives. MRR is the
reciprocal rank of the first relevant result, or zero when absent from top 3.
Language groups use different subjects, not controlled translations.

Against RRF, score fusion improved recall on 16 questions and worsened none;
MRR improved on 18 and worsened on 3. The same-language MRR decline is real.
Against vector-only, score improved no question's recall and worsened one;
MRR improved on 2 and worsened on 11. The candidate therefore **does not beat
vector-only on this holdout**.

All three RRF-relative MRR regressions are questions 001, 003 and 017: the
keyword rank-1 bonus overrode a stronger relevant cosine match, favoring a
cancellation over a reschedule or the wrong sensor code. Question 038 is the
vector-relative recall regression: same-language return policies gained enough
keyword bonus to push the relevant English fabric-return exception from
vector rank 2 to score rank 4. Bounded keyword bonuses reduce the original
intersection problem but do not solve intent, negation or exact-identifier
reasoning. All paired changes and top-3 results/candidate fingerprints are
retained in the packaged evidence. The larger full-candidate archive remains
separately retained; it is not part of the compact source distribution.

The old development pipeline reports and default RRF rankings stayed unchanged.
Both holdout reports reproduced byte-for-byte in the same environment, and
all full control-channel fingerprints match. Independent AI review recalculated
metrics from the rankings and checked every recorded regression. This does not
turn the labels into human annotations or establish production reliability.

Keep the default unchanged. This opt-in candidate substantially reduced RRF's
cross-language recall loss on this synthetic set, but representative application
data should decide between RRF, score fusion and vector-only. See the
[retained study evidence and dataset limitations](../eval/evidence/README.md).

Even a favorable result would not establish statistical significance, model
independence, production quality, security, numeric constraint satisfaction,
latency or answer-generation correctness. Keep the old default until the
application owner chooses a mode using representative data and requirements.
