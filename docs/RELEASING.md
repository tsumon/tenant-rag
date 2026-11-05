# Release checklist

## Version 0.5.1 publication

The maintainer authorized this patch's bilingual local trial, CI, merge and GitHub
Release. The new `release-0.5.1.yml` uses the one-time publication pattern below;
historical workflows must remain unchanged. Core runtime code is unchanged.

Before packaging, run `python examples/local_trial/verify_evidence.py` as well as
`python scripts/verify_evidence.py`. The trial pins the official 0.5.0 wheel for
reproducibility; independently verify installed 0.5.1 distributions. No model
weights are downloaded in the core/release matrix. Dedicated trial CI covers the
optional model stack. Inspect the source archive for the example's scripts,
bilingual documentation, synthetic data and evidence, and for absence of weights,
wheels, virtual environments and databases.

The release has the same six-asset contract as 0.5.0. To verify downloaded 0.5.1
assets: `python scripts/verify_release_download.py DOWNLOAD_DIR FULL_COMMIT_SHA 0.5.1`.
Omitting the version retains the historical 0.5.0 verification contract. This
checks package metadata without extracting or executing archive contents. Install
and run smoke tests in a separate step without release credentials. Check the
same draft identity, exact main SHA, tag and every asset again before and after
publication. Never overwrite an existing or uncertain draft/tag/release.

See [0.5.1 release notes](RELEASE_0.5.1.md). This authorization does not extend to
later releases, PyPI or deployment.

## Version 0.5.0 publication

The maintainer explicitly authorized the 0.5.0 merge and GitHub Release. The
version-specific `release-0.5.0.yml` follows the existing repository publication
pattern: its change on main runs the full core matrix, then builds at that exact
commit. It refuses an existing tag/release, uploads a draft, downloads and checks
all assets and installation smoke tests, and publishes only after verification.
This is one-time authorization for 0.5.0, not future releases. No PyPI upload or
deployment is included. Do not reuse or modify historical release workflows to
publish another version.

See [release notes](RELEASE_0.5.0.md), [0.5 compatibility](V0.5.md),
[CHANGELOG](../CHANGELOG.md), current dependency advisories, public-data provenance
and the final exact-SHA checks before publication. If a run fails, inspect the
existing draft/tag/assets before any retry; never overwrite an uncertain release.

## Distribution

Version 0.5.0 is distributed through [GitHub Releases](https://github.com/tsumon/tenant-rag/releases/tag/v0.5.0), with wheel, source distribution, offline pipeline report, evidence-verification log, source commit and SHA-256 checksums. The release workflow tests Python 3.10–3.14 and smoke-tests installed distributions before publication.

Known validation gaps remain: full browser interaction QA, representative production quality, production load and adversarial security audit. HTTP/admin proxy smoke tests do not replace browser QA.

## Local gates

1. Use Python 3.10+ with FTS5 trigram support.
2. Run `python -m unittest discover -v` and `python demo/run_demo.py`.
3. Run `python eval/run_eval.py --k 3 --min-recall 0.8 --min-mrr 0.7`. This is an offline pipeline gate, not a semantic quality claim.
4. Build with `python -m pip install build==1.3.0 && python -m build`.
5. Install the wheel into a clean environment outside the checkout. Check both console commands, then run `path/to/venv/bin/python -I scripts/smoke_installed.py` to verify bundled admin HTML and an HTTP register/index/search/delete round trip through the admin proxy. The isolated interpreter prevents checkout imports.
6. Install/build the source distribution independently, to catch missing package assets.
7. Exercise admin login failure/retry, upload, pagination, search, delete/cancel and logout in a browser. Do not equate HTTP tests with browser QA.
8. Restore a backup and test legacy migration against a copy, never the only production database.

## Before public release

- Review the exact diff, license, version and bilingual docs. Validate CI on each supported Python version; local tests on one interpreter do not validate the matrix.
- Review provider privacy/cost requirements. If claiming semantic quality, run a named real model on a representative consented dataset and archive model/configuration/results. Never use fake embeddings as evidence.
- Document platform-specific limits, deployment security and remaining limitations.
- Publishing a package, pushing commits, creating tags/releases and deploying require separate authorization. This checklist does not perform them.

## 0.3.0 changes

- Installable standard-library package with API/admin console commands and bundled admin HTML.
- Offline CI test/evaluation/build gates and wheel smoke test.
- Explicit provider evaluation mode, structured results and separate semantic challenge fixtures.
- Provider response validation and protocol regression tests.
- Admin body-size/timeout/route handling and Retry-After forwarding.
- Migration index preservation, context-manager cleanup, server shutdown cleanup and rate configuration validation.
- Runnable source quickstart and explicit logical-isolation, embedding-migration and backup boundaries.

## 0.4.0 changes

- Persisted endpoint/model/revision and dimensions; explicit legacy adoption or atomic database-wide reindex.
- Deterministic text/Markdown chunks with Unicode offsets and source metadata.
- Atomic incremental ingestion, parent updates/deletes, tenant-scoped reuse and parent quotas.
- Input, overlap and repeated metadata amplification limits.
- Reproducible synthetic evaluation corpus and no-network provider request planning.
- Upgrade requires stopping old clients, verifying a backup and following [the migration guide](V0.4.md).
- Release verification installs both wheel and source distribution, then downloads and verifies published assets and repeats installation smoke tests.
