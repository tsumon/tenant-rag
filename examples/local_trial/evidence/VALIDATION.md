# Trial validation and limits

The retained final and repeat JSON reports were produced with the installed,
verified official **v0.5.0 wheel**, not the repository checkout. The enclosing
repository v0.5.1 adds this example; core retrieval behavior is unchanged.

## Fixed inputs and process

- Release source commit: `5a52e0a18edcb145e66a03b0a84aeea15b0e6f4b`.
- Wheel SHA-256: `4402ea42af53d4d1fd02de8a0baa0d912436e187495c8c394ffd1743bbbaf76c`.
- Model: official multilingual MiniLM revision and twelve-file hashes in
  `../model-manifest.json`. Safetensors only; no model Python or pickle files.
- The ten synthetic documents and sixteen questions were frozen before first
  inference. Source and label bytes have not been changed to improve results.
  The fixture README later clarified that model acquisition is a separate setup
  step; this did not change data, labels, or reported retrieval.
- Fixed chunks: 180 Unicode characters, overlap 30. Fixed score weight: 0.2.
- Linux CPU, Python 3.12.14, SQLite 3.53.1. Full observed package versions are in
  `environment-freeze.txt`; direct setup pins are not a full transitive lock.
- Official downloads and clean virtual-environment installation were exercised.
  The installed package bytes and actual import location were verified against
  the release wheel. No copied checkout package was used for these reports.
- `real-model-final.json` and `real-model-repeat.json` are byte-identical in this
  environment. Their hashes are in `repeat-checksums.txt`. This is not a promise
  of identical floats on other hardware. Timings are separate and are not an SLA.

## Checks

The fifteen example mechanics tests pass. They deliberately use fake embeddings
for fast invariants; model-quality evidence comes from the real-model JSON files.
Every source citation and reported metric is also checked without model downloads
by `python verify_evidence.py`. CI reruns this verifier, the mechanics tests,
and a separate actual local-model scenario using the official v0.5.0 wheel.

The real scenario checks both tenants' source membership and exact excerpts,
32 opposite-key query runs, invalid-key rejection before embedding, parent quotas
and listings, unchanged retry without embedding, same-ID deletion isolation,
deleted-chunk absence, and persistence after reopening. Maximum input length
was 72 chunk tokens and 20 query tokens, below the 128-token model limit.

A separate review recomputed metrics and source slices and independently reran
the real scenario. It found and prompted fixes for extra unlisted fixture files,
import shadowing, and accidental timing-file overwrite. This is code review and
reproduction, **not external adopter feedback** or a penetration test.

## Results that must remain visible

Default RRF retrieves a relevant parent at rank one for all fourteen answerable
questions, but complete labeled evidence for only **12/14**. In q01/q02 it finds
the refunds document but the wrong chunk. Score fusion returns **14/14** complete
labeled sentences here; broader public release studies found worse recall for
score fusion, so this tiny example does not justify changing the default.

Both modes return hits for **both out-of-scope questions**. There is no abstention
policy, generated answer, or answer-faithfulness claim. The bilingual fixture is
not a pure cross-language benchmark. Exact English-sentence matching may score
an equivalent Chinese passage as missing.

Offline library flags and a Python socket audit guard recorded zero network
attempts during inference. This is not an OS-level firewall or native-code trace.
Shared SQLite tenant filtering is not encryption, physical separation, or
protection against someone who can read the database file. No real private data,
production load, HTTP/browser flow, or document-level ACL has been validated here.
