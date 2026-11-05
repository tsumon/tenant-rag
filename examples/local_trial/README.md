# Two tenants, real local embeddings, verifiable excerpts

A copyable **tenant-rag v0.5.0** trial for a fictional two-store support search.
It installs the official GitHub release wheel, imports Markdown, runs a real
multilingual embedding model on CPU, and returns exact source excerpts with
character offsets. No LLM answer generation, API key, paid service, real customer
data, public server, or publishing is involved.

[中文步骤与结果](README.zh-CN.md)

## What is included

- Ten short synthetic English/Chinese Markdown sources, five each for Aurora
  and Cedar. Identical parent IDs intentionally have different policy values.
- Sixteen frozen questions: fourteen answerable and two out-of-scope.
- Pinned official v0.5.0 wheel download and checksums/source commit; no checkout import.
- Explicit, checksum-verified model download script. The approximately 500 MB
  model and wheel are **not** committed or included in the source archive.
- An offline trial, tests, complete retrieval reports, and source excerpts.

## Run on a Linux CPU machine

Tested here with Python 3.12.14 and SQLite 3.53.1. Other OS/Python combinations
are not validated by this example. SQLite must support FTS5 with the trigram
tokenizer. Use a new virtual environment and enough disk space for the model,
CPU PyTorch, and dependencies. Setup requires Internet access; inference does not.

From a repository checkout or the extracted source distribution:

```sh
cd examples/local_trial
```

This trial deliberately stays pinned to the already published **v0.5.0** wheel.
The repository release v0.5.1 adds this example without changing core retrieval.
The v0.5.1 wheel is validated separately by the release smoke tests.

Then run:

```sh
python3 -m venv .venv
. .venv/bin/activate
unset PYTHONPATH
python -m pip install 'torch==2.13.0' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-local.txt
python scripts/download_assets.py --model-dir model
python -m pip install --no-deps wheel/tenant_rag-0.5.0-py3-none-any.whl
python -m unittest discover -s tests -v
python run_trial.py --model-dir model --output my-run.json
```

The downloader contacts only GitHub/Hugging Face official source URLs and their
asset CDNs. It downloads no model Python or pickle weights. It verifies pinned
Git blob or LFS hashes, refuses to overwrite an invalid existing file, and skips
valid cached files. Run it again to resume completed files after interruption.
The model directory must contain exactly the pinned twelve files, not extra
training files or arbitrary code. The model's published license is Apache-2.0.

`run_trial.py` refuses an existing report or companion timing output path. Pick a new name for a repeat.
It verifies installed package files against the pinned official wheel, verifies
the imported package location, all model files, and the exact frozen fixture file
set, then creates a temporary database and random
in-memory tenant keys. The database and keys are not delivered or retained.

After setup, repeat with networking disabled if available. This script also sets
offline library flags and denies Python socket connect/DNS/sendto audit events.
The recorded run had zero attempted events. This is a Python-process check,
**not an OS-level network sandbox** or a claim about arbitrary native libraries.
There is no automatic fallback to remote embeddings.

## Read the example

1. `load_data` assigns each Markdown file a tenant, parent ID, and source name.
2. `TenantRAG.ingest_batch` chunks and imports each tenant independently.
3. An unchanged retry proves no additional embedding calls occur.
4. Search uses the corresponding tenant key and the installed public API.
5. `citation` checks every returned excerpt against its exact original Unicode
   character slice, including tenant, parent ID, source, and `[start, end)`.
6. The report compares default RRF with experimental score fusion at the fixed
   default weight 0.2. Neither labels nor settings are changed to improve scores.

The same free-delivery question has different evidence: Aurora requires
**60 credits**, Cedar **90 credits**. All policies are invented demonstration
content, not promises from a real business.

The copied `local_embedder.py` is the MIT-licensed evaluation adapter from
v0.5.0. It is not part of the installed wheel. The example uses only its supported
local Transformer + Pooling loader, `trust_remote_code=False`, safetensors,
normalized embeddings, and CPU. The core tenant-rag package remains unmodified.

## Measured result and the important failure

The final reports are in `evidence/`. Fourteen answerable questions, top **three
raw chunks**, fifty chunks in total:

| Measurement | Default RRF | Score fusion, weight 0.2 |
| --- | ---: | ---: |
| Relevant parent Recall@3 chunks | 14/14 | 14/14 |
| Relevant parent MRR@3 chunks | 1.000 | 1.000 |
| Exact labeled evidence sentence present | 12/14 | 14/14 |
| Out-of-scope questions returning a hit | 2/2 | 2/2 |

**Finding the right document does not mean finding the answer.** RRF finds the
refunds document first for both return-window questions, but returns the part
about shipping charges instead of the 21-day or 35-day return window. These are
q01 and q02, recorded in full in the JSON reports. A parent-only metric hides
this failure; the exact-evidence metric exposes it.

Both modes return excerpts for the two out-of-scope questions. Retrieval is not
an answerability classifier. Do not present a nearest match as a supported answer
or interpret ranking scores as confidence. This example does not generate an
answer or implement abstention.

Score fusion does better on this tiny case, but that is **not** a general quality
claim. The release's broader [public evaluation](https://github.com/tsumon/tenant-rag/blob/v0.5.0/eval/public-study/README.md)
found worse recall for score fusion on both public datasets. RRF stays the default.

## What was actually checked

- Two tenants reuse the same five parent IDs in one SQLite database.
- All normal-query results and 32 opposite-key query runs are checked for
  tenant membership and exact source offsets, in both retrieval modes.
- Invalid keys fail before embedding; lists and parent quotas remain scoped.
- Deleting a parent in Aurora removes its chunks and leaves Cedar unchanged.
- Closing and reopening preserves data and deletion.
- Reimporting unchanged sources makes zero new embedding calls.
- All fifty chunks and sixteen queries fit the model's 128-token limit.
  Observed maxima: 72 chunk tokens, 20 query tokens; no silent truncation.
- Unit tests use fake embeddings only for mechanics; the reports explicitly use
  the real pinned model. These two kinds of evidence are kept separate.

## Reproducibility and boundaries

Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, revision
`e8f8c211226b894fcb81acc59f3b34ba3efd5f42`. Twelve-file fingerprint:
`a3f7688b8991a6ea9f048524205c08de9c362f0e5d63d0ebdc9116a563601529`.

Wheel SHA-256:
`4402ea42af53d4d1fd02de8a0baa0d912436e187495c8c394ffd1743bbbaf76c`.
Source commit: `5a52e0a18edcb145e66a03b0a84aeea15b0e6f4b`.
Release: https://github.com/tsumon/tenant-rag/releases/tag/v0.5.0

Chunking is fixed at 180 Unicode characters with 30-character overlap. This is
not token-based chunking. The script checks token lengths separately and fails
if the model would truncate a chunk or query. Citation offsets are Unicode
character indexes, not bytes, graphemes, or token indexes. A matching citation
only proves that the excerpt exists at that source location, not that it answers
the question or that the source is trustworthy.

This is a tiny hand-authored demonstration, not an independent benchmark or
evidence of external adoption. Bilingual documents include Chinese equivalents,
so Chinese-query results are not a pure cross-language test. Exact evidence
matching is intentionally strict; equivalent Chinese evidence can be marked as
missing. Chunk duplicates keep their original rank; the metrics do not silently
deduplicate or search past the first three hits.

Logical tenant filtering is not encryption, physical separation, or a security
audit. Whoever can read the SQLite file can read all tenants. A leaked bearer
key grants tenant access. The example does not test malicious hosts, production
load, real private data, document-level permissions, user management, PDF parsing,
HTTP/browser workflows, or generated-answer faithfulness. The package's public
HTTP service is not started. Measured local timings are illustrative, not an SLA.

The three principal model dependencies are pinned at the release's validated
versions, with setuptools pinned separately for consistent packaging tooling.
Transitive dependencies are not fully constrained; the full measured
environment is recorded in `evidence/environment-freeze.txt`. Versions are not
guaranteed permanently vulnerability-free. Consult current advisories before deployment.
Exact floating-point ranks may differ on other hardware/library builds.

## Files to inspect first

- `run_trial.py`: complete import/retrieval/isolation/citation workflow.
- `data/queries.json`: frozen labels including the two unanswerable questions.
- `evidence/real-model-final.json`: every result, metric, hash, and check.
- `evidence/real-model-repeat.json`: same-environment repetition.
- `evidence/VALIDATION.md`: test scopes and independent review disposition.
- `model-manifest.json`, `release-manifest.json`: official immutable provenance.

License: MIT for this example and copied adapter. Model license is separate.
