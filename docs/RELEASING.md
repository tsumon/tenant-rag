# Release checklist

Version 0.3.0 is a release candidate in source, not evidence of a published package or deployed service.

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
