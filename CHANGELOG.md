# Changelog

## 0.5.1 (2026-11-05)

- Add a bilingual, reproducible local real-model trial with synthetic data,
  checked-in evidence and offline evidence verification.
- Pin the trial to the official 0.5.0 wheel for historical reproducibility;
  independently smoke-test the 0.5.1 wheel and source distribution.
- Package trial source, documentation and evidence without weights, wheels,
  environments or databases. Add dedicated real-model CI and release gates.
- No core runtime, API or database schema changes. Retrieval still returns
  ranked fragments for unanswerable questions; this is not answer generation.

## 0.5.0 (2026-11-05)

- Add explicitly selected cosine-score/keyword-rank fusion in SDK, HTTP and
  evaluation tools, preserving the default RRF behavior and tenant checks.
- Add trusted local safetensors evaluation with source fingerprints, strict
  same-input report comparisons and independent synthetic retrieval evidence.
- Reject incomplete/corrupt comparison metadata, invalid hash encodings and
  oversized numeric metrics without producing a misleading comparison.
- Document long-input truncation, numeric-reasoning limits, shared FTS statistics
  and the difference between core and optional model-stack compatibility.
- Update the optional model dependency baseline after reviewing current
  advisories. Do not redistribute weights/environments as part of this package.
- No database schema change or required 0.4.0 reindex for fusion-mode selection.

## 0.4.0

Model identity validation, explicit legacy adoption, atomic reindex, deterministic
text/Markdown chunks, source offsets, parent lifecycle and tenant quotas.

## 0.3.0

Installable standard-library package, HTTP/admin entry points, reproducible
pipeline checks, supported-Python CI and distribution smoke tests.
