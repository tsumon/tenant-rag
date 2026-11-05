# Retained evaluation evidence

The source distribution and repository include two compressed archives; wheel
runtime packages do not include model weights or public corpora. Both archives
have JSON sidecars containing SHA-256 and scope. Extraction is unnecessary for
`scripts/verify_evidence.py` to check the synthetic archive and original labels.

- `synthetic-holdout-v1.zip`: unchanged 132-document / 64-query synthetic corpus,
  per-question labels, pre-result audit, authoring source, recorded freeze and
  original raw reports. These labels were AI-authored and independently
  AI-reviewed, **not human annotations**. This is now a consumed regression set.
- `release-model-results.zip`: every public query's original ID/known positives,
  actual rankings, per-query and slice metrics, all paired changes, token-length
  audits and measured costs; no source passages. It also contains the patched
  model-stack reruns of golden/operations/holdout. All old per-query rankings and
  metrics stayed unchanged. Runtime/report fingerprints differ, so these are
  separately recorded reruns rather than a claim of byte-identical old reports.
- `dependencies/`: the actual Linux/Python 3.12 lock, two complete advisory scans
  and license inventory. See its README for security and redistribution limits.

The local pre-holdout commit `938ceffb382ed550e9eeeb9e850a54459aa31e65` has the
same tree as published `5c567a508e8cec94b2aee6392fac14c446aeec16`. Commit hashes
change with publication metadata; tree/content hashes establish identical code.
Recorded times and hashes do not independently prove when an author viewed data.
The original source/label preparation and no-retuning protocol are preserved.

For results and reproduction read [fusion evidence](../../docs/FUSION.md) and
[the public study](../public-study/README.md). To rerun synthetic inputs, extract
the corpus/manifest from the archive into a fresh directory and use
`eval/run_eval.py --embedder local --model-path ... --golden ... --manifest ...
--gate-suite semantic --fusion rrf` (then score with weight 0.2). Use the patched
locked environment, offline flags, and an existing verified model snapshot.
No model/data acquisition or inference runs during ordinary core CI.

No metrics demonstrate production correctness, universal fusion superiority,
numeric constraint satisfaction or answer-generation quality. Score remains an
experimental opt-in; the default RRF is unchanged.
