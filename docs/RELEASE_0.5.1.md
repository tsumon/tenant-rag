## tenant-rag 0.5.1

A documentation and example patch release. Core retrieval code, API, database schema,
and the dependency-free core runtime are unchanged from 0.5.0. Distributed through
GitHub Releases, not PyPI; this release does not deploy a service.

### Reproducible local trial

- English and Chinese walkthroughs, synthetic documents and queries, local-model
  instructions, checked-in observations and an offline evidence verifier under
  [`examples/local_trial`](https://github.com/tsumon/tenant-rag/tree/v0.5.1/examples/local_trial).
- The historical trial deliberately installs the official **0.5.0 wheel**. Its
  pinned package/model/data inputs preserve reproducibility; it is not a claim
  that a model run tested 0.5.1. The release independently installs and smoke-tests
  the **0.5.1 wheel and source distribution** across Python 3.10–3.14.
- Dedicated CI covers the optional real-model trial separately from the offline
  core and release matrix. The source distribution includes the trial source,
  documentation, synthetic data and compact evidence, not model weights,
  downloaded wheels, virtual environments or databases.

### Limits that remain

This is retrieval, not answer generation. Unanswerable questions can still return
ranked fragments; there is no guaranteed abstention or factual-answer validation.
Synthetic examples are not representative production-quality evidence. Model
truncation, numeric reasoning, shared FTS ranking statistics and logical rather
than physical tenant isolation remain relevant. Score fusion remains experimental
and RRF remains the default. See [0.5.0 quality limits](https://github.com/tsumon/tenant-rag/blob/v0.5.1/docs/RELEASE_0.5.0.md).

### Install and verify

Download the wheel and `SHA256SUMS` from this release, check the wheel's SHA-256,
then install `tenant_rag-0.5.1-py3-none-any.whl` with pip. Requires Python 3.10+
and SQLite FTS5 trigram support. No schema migration or reindex is required from
0.5.0. Older upgrade requirements remain in [V0.5.md](https://github.com/tsumon/tenant-rag/blob/v0.5.1/docs/V0.5.md).

The six release assets are the wheel, source distribution, `offline-eval.json`,
`evidence-verification.txt`, `SOURCE_COMMIT.txt` and `SHA256SUMS`. The source commit
identifies the exact build. The workflow verifies all five payload checksums,
package versions and source commit, installs both downloaded package formats
without release credentials, then publishes only the same verified draft and
checks the public assets/tag. The offline evaluator is a pipeline gate, not a
semantic-quality result. No PyPI publication is included.
