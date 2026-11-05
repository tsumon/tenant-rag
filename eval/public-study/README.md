# Public retrieval study (2026-11-05)

This release keeps RRF as the default and exposes score fusion as an experimental
opt-in. **The public-data results do not support replacing RRF with score 0.2.**
No weights, algorithms or source labels were changed after seeing these results.

## Original labels and fixed selection

- English: the full BEIR SciFact corpus (5,183 abstracts) and all 300 test queries,
  with 339 original positive query/document pairs.
- Chinese: 128 C-MTEB T2Retrieval dev queries selected by a declared SHA-256 rule,
  all 633 source-judged documents plus 2,048 hash-selected distractors, total
  2,681 documents. This is a **reduced-corpus diagnostic**, not an official
  C-MTEB leaderboard score. Removing competing documents can inflate results.
- Original source IDs, texts and binary qrels are preserved. Source versions and
  checksums are pinned in `SOURCE_FILES.json` / `download_public_data.py`;
  `SELECTION_PLAN.json` records the outcome-blind query and distractor selection.
- English title and abstract are joined by one newline. No text cleaning,
  generated labels, translations, numerical substitutions or padding is used.
  Public passages stay outside this repository and release artifacts.
- These same-language public benchmarks complement the prior AI-synthetic
  cross-language tests. Model training contamination is unknown. They are not
  independent human annotation commissioned for this project.

## Results at k=3

| Dataset / mode | Recall@3 | MRR@3 |
| --- | ---: | ---: |
| SciFact: RRF | 0.6202 | 0.5444 |
| SciFact: score 0.2 | 0.5992 | 0.5567 |
| SciFact: vector | 0.4464 | 0.4256 |
| SciFact: FTS | 0.6717 | 0.6178 |
| T2 subset: RRF | 0.5029 | 0.8737 |
| T2 subset: score 0.2 | 0.4665 | 0.8411 |
| T2 subset: vector | 0.3909 | 0.7174 |
| T2 subset: FTS | 0.3962 | 0.7643 |

Score loses recall to RRF on both corpora. In SciFact it improves recall on 10
questions and worsens 20; MRR improves on 31 and worsens 30. In T2 it improves
recall on 6 and worsens 22; MRR improves on 5 and worsens 12. FTS-only is best
in the SciFact aggregate. All gains and losses are retained in the evidence.

Recall averages each query's fraction of known relevant documents recovered.
T2 has 1–18 positive documents per query; 79/128 questions have more than three.
Its maximum possible macro Recall@3 is therefore 0.70206, not 1.0. MRR considers
only the first relevant hit. SciFact's recall ceiling is 0.99483. Missing qrels
are not proof that an unjudged result is irrelevant. These are not standard
BEIR nDCG@10 scores and no statistical-significance claim is made.

## Numeric and long-text slices

`query_numeric` means an ASCII digit occurs in the original question. It does
**not** certify a numerical constraint or arithmetic challenge. Score/RRF
Recall@3 is 0.5731/0.6012 on 114 English digit-containing queries and
0.5801/0.5801 on 17 Chinese queries. Existing synthetic numeric-condition
regressions remain relevant; this library does not apply typed numeric filters.

The long-positive-document slice means at least one relevant source document
has >=2,000 Unicode characters. Score/RRF Recall@3 is 0.7128/0.7419 on 74 English
queries and 0.4023/0.4630 on 35 Chinese queries. These are overlapping descriptive
slices, not separately selected successes.

Whole-document `index()` was deliberately used with the unchanged 128-token
MiniLM limit. **5,123/5,183 English documents (98.8%) and 2,272/2,681 Chinese
documents (84.7%) exceeded that limit**; all queries fit. Maximum document
lengths were 2,256 and 11,268 tokens. FTS sees the complete text while the encoder
sees a truncated prefix. This is a material limitation of this configuration,
not proof that the underlying benchmark or fusion formula is defective.
No chunked full-corpus improvement is claimed. Use explicit ingestion and
token audits for long documents; see [the upgrade guide](../../docs/V0.5.md).

## Model and observed resource cost

The official multilingual MiniLM revision is
`e8f8c211226b894fcb81acc59f3b34ba3efd5f42`; the verified model snapshot contains
499,561,295 bytes. CPU model runtime is ST 5.6.0 / Transformers 5.13.0 /
torch 2.13.0+cpu, Python 3.12.14, two threads per process. The installed optional
environment occupies about 1.3 GiB; no such environment is bundled.

Observed batched document embedding took about 282 seconds for SciFact and
155 seconds for T2. Peak process RSS was about 1.52 and 1.49 GiB. Cached-vector
score search p50/p95 was about 309/374 ms and 157/206 ms respectively. These
search measurements **exclude model inference**. A separate fixed-ID probe
of 16 warm single-query model calls observed p50/p95 about 25/39 ms (three
warmup calls excluded); this is not an end-to-end service measurement. The two studies ran partly
concurrently on the same cloud machine, alongside validation work, so these are
resource observations, not dedicated-host throughput or production latency
benchmarks. SQLite database/WAL sizes were about 66.7 MB and 48.3 MB.

No hosted embedding API requests or API charges occurred. CPU time, memory and
infrastructure are not thereby free. No infrastructure price or energy estimate
is claimed. Inference used offline flags plus a Python socket-denial guard and
recorded zero outbound attempts; this is not an OS network sandbox.

## Reproduction

On Linux x86_64 (the runner uses Linux `resource` RSS accounting), from the
repository root, use separate approved environments for data conversion
(PyArrow 26.0.1) and the [verified model stack](../evidence/dependencies/README.md).
Data acquisition is an explicit network step, not part of inference:

```sh
python eval/public-study/download_public_data.py --output /tmp/public-data
python eval/public-study/convert_public_data.py --root /tmp/public-data
python eval/public-study/verify_public_data.py --root /tmp/public-data
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false \
python eval/public-study/run_public_eval.py \
  --golden /tmp/public-data/scifact-test-full/golden.jsonl \
  --manifest /tmp/public-data/scifact-test-full/manifest.json \
  --model-path /path/to/verified/model --output-dir /tmp/new-scifact-run
python eval/public-study/analyze_public.py /tmp/new-scifact-run
```

Repeat with `t2retrieval-dev-hash128`. Outputs must be new directories, so prior
evidence is not overwritten. A different model/runtime is a new experiment.
The runner indexes through `TenantRAG.index` and searches via the real SDK.
Batched real embeddings are cached in memory; all documents remain candidates.
Original labels/IDs, full top-3 results, every slice, token lengths and observed
costs are retained in the compressed [evidence archive](../evidence/README.md).

## Source licenses and attribution

SciFact component licensing is nuanced: original claims/annotations are
CC-BY-4.0 and the abstract database is ODC-By-1.0. BEIR's separate card declares
CC-BY-SA-4.0; it is retained as metadata, not treated as replacing component
licenses. See [the fixed upstream license](https://github.com/allenai/scifact/blob/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e/LICENSE.md)
and [dataset card](https://huggingface.co/datasets/BeIR/scifact/blob/b3b5335604bf5ee3c4447671af975ea25143d4f5/README.md).
T2Ranking authors state [Apache-2.0](https://github.com/THUIR/T2Ranking/blob/3ab0a0de72dd50bf84d852a985f6188334781403/README.md#license);
C-MTEB's published retrieval adaptation supplies the exact binary qrels used.
We do not reconstruct or claim the upstream four-level reranking grades.
Downloading/converting data does not confer model or dataset ownership.
