## tenant-rag 0.5.0

SQLite-backed, tenant-scoped retrieval with zero third-party core runtime dependencies. Distributed through GitHub Releases, not PyPI; this is not a deployment.

### Changes

- Opt-in score-aware fusion in the SDK, HTTP search and evaluator. **RRF remains the default.**
- Restrictive local safetensors model loading, model/data/code fingerprints and strict report comparisons.
- Reproducible real-model public/synthetic evaluations, per-query evidence, dependency lock and license inventory in the source distribution.
- Hardened comparison validation, 215 regression tests and Python 3.10–3.14 core CI.

### Quality limits

**Score fusion is experimental, not a generally better replacement for RRF.** With fixed multilingual MiniLM weights and k=3, English SciFact (300 queries) score Recall/MRR is 0.5992/0.5567 versus RRF 0.6202/0.5444. The deterministic Chinese T2 subset (128 queries) scores 0.4665/0.8411 versus RRF 0.5029/0.8737. Score loses recall on both; Chinese MRR also declines. FTS is strongest in aggregate on SciFact.

98.8%/84.7% of English/Chinese passages exceed this model's 128-token limit. These are whole-document observations, not evidence that long-document chunking or numeric constraints are solved. Separate synthetic cross-language improvements do not establish production generalization. See the [full public results and costs](https://github.com/tsumon/tenant-rag/blob/v0.5.0/eval/public-study/README.md).

### Install and upgrade

Download the wheel and `SHA256SUMS`, verify the SHA-256, then run:

`python -m pip install tenant_rag-0.5.0-py3-none-any.whl`

Requires Python 3.10+ and SQLite FTS5 trigram support. From 0.4, the database schema and default behavior are unchanged; changing fusion mode needs no reindex. Back up and verify restoration, stop old service processes, and read the [upgrade and rollback guide](https://github.com/tsumon/tenant-rag/blob/v0.5.0/docs/V0.5.md). Older databases still require explicit model adoption/reindex. No model weights or third-party environments are bundled.

Optional model evaluation was verified only on Linux/Python 3.12 with ST5.6.0, Transformers5.13.0 and Torch2.13.0+cpu. Both PyPI and OSV scans covered all 42 installed model-environment packages with zero known matches and zero skips on 2026-11-05; a dated scan is not a security guarantee. Core CI compatibility is separate from the optional stack.

The release workflow verifies tests/evidence/builds and both installed package formats across the core matrix. It downloads draft assets, checks every checksum and source commit, runs isolated HTTP/admin smoke tests, and only then publishes. `SOURCE_COMMIT.txt` records the exact build commit. `offline-eval.json` is a deterministic pipeline gate, not semantic model-quality evidence.

Logical tenant filtering is not physical or ranking isolation: shared FTS statistics can affect ranks. Full browser UI, representative production quality/load, adversarial security and generated-answer accuracy are not certified. Use appropriate trusted-environment controls.
