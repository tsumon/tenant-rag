# Evaluating cross-language retrieval and fusion

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

## Fixed model and evaluation procedure

- Official model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
- Upstream revision: `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`.
- Official safetensors SHA-256:
  `eaa086f0ffee582aeb45b36e34cdd1fe2d6de2bef61f8a559a1bbc9bd955917b`.
- Sentence Transformers 3.4.1, Transformers 4.48.3, torch 2.6.0+cpu.
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

Even a favorable result would not establish statistical significance, model
independence, production quality, security, numeric constraint satisfaction,
latency or answer-generation correctness. Keep the old default until the
application owner chooses a mode using representative data and requirements.
