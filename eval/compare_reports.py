"""Compare compatible evaluation reports without running an embedding model."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

MODES = ("hybrid", "vector", "fts")
METRICS = ("recall_at_k", "mrr_at_k")


def _metrics(value):
    for mode in MODES:
        for name in METRICS:
            score = value[mode][name]
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("report metrics must be finite numbers between zero and one")


def _validate_rankings(report):
    """Reject corrupted metrics instead of presenting misleading deltas."""
    if type(report['k']) is not int or report['k'] < 1:
        raise ValueError("report k must be a positive integer")
    for query in report['queries']:
        expected = query['expected']
        if (not isinstance(expected, list) or not expected
                or any(not isinstance(doc_id, str) or not doc_id for doc_id in expected)
                or len(set(expected)) != len(expected)):
            raise ValueError("report has invalid relevance labels")
        for mode in MODES:
            ranked = query['ranked'][mode]
            if (not isinstance(ranked, list) or len(ranked) > report['k']
                    or any(not isinstance(doc_id, str) or not doc_id for doc_id in ranked)
                    or len(set(ranked)) != len(ranked)):
                raise ValueError("report has invalid rankings")
            recall = len(set(expected) & set(ranked)) / len(expected)
            mrr = next((1 / index for index, doc_id in enumerate(ranked, 1)
                        if doc_id in expected), 0)
            for name, actual in (('recall_at_k', recall), ('mrr_at_k', mrr)):
                if not math.isclose(actual, query['metrics'][mode][name], abs_tol=1e-12):
                    raise ValueError("report query metrics disagree with its rankings")
    if set(report['suites']) != {q['suite'] for q in report['queries']}:
        raise ValueError("report suites disagree with its queries")
    for name, suite in report['suites'].items():
        selected = [q for q in report['queries'] if q['suite'] == name]
        if suite['queries'] != len(selected):
            raise ValueError("report suite query count is inconsistent")
        for mode in MODES:
            for metric in METRICS:
                average = sum(q['metrics'][mode][metric] for q in selected) / len(selected)
                if not math.isclose(average, suite['metrics'][mode][metric], abs_tol=1e-12):
                    raise ValueError("report suite metrics disagree with its queries")


def compare_reports(baseline, candidate):
    """Require the same corpus, selection, implementation and retrieval setup.

    Package/runtime changes are reported explicitly. This compares observed
    runs, not a controlled statistical attribution to a particular model.
    """
    try:
        for report in (baseline, candidate):
            if report["schema_version"] != 2:
                raise ValueError("comparison requires schema-version 2 reports")
            for key in ("sha256", "selected_records_sha256"):
                if not isinstance(report["dataset"][key], str) or len(report["dataset"][key]) != 64:
                    raise ValueError("report is missing a dataset fingerprint")
            if not report["queries"] or not report["implementation_sha256"]:
                raise ValueError("report must contain queries and implementation fingerprints")
            if len({q["id"] for q in report["queries"]}) != len(report["queries"]):
                raise ValueError("report contains duplicate query IDs")
            for query in report["queries"]:
                _metrics(query["metrics"])
            for suite in report["suites"].values():
                _metrics(suite["metrics"])
            _validate_rankings(report)
        for field in ("k", "candidates_per_channel", "retrieval", "implementation_sha256"):
            if baseline[field] != candidate[field]:
                raise ValueError(f"reports differ in {field}; rerun both with identical evaluation code/settings")
        for field in ("sha256", "selected_records_sha256"):
            if baseline["dataset"][field] != candidate["dataset"][field]:
                raise ValueError(f"reports differ in dataset {field}; corpus and selected queries must match")
        old = {q["id"]: q for q in baseline["queries"]}
        new = {q["id"]: q for q in candidate["queries"]}
        if old.keys() != new.keys() or baseline["suites"].keys() != candidate["suites"].keys():
            raise ValueError("reports must contain identical queries and suites")
        changes = []
        for key, left in old.items():
            right = new[key]
            for field in ("suite", "query", "expected", "tags"):
                if left[field] != right[field]:
                    raise ValueError("reports disagree on query text, labels, tags or suite")
            delta = {mode: {metric: right["metrics"][mode][metric] - left["metrics"][mode][metric]
                            for metric in METRICS} for mode in MODES}
            if any(value for metrics in delta.values() for value in metrics.values()):
                changes.append({"id": key, "suite": left["suite"], "delta": delta,
                                "baseline_ranked": left["ranked"], "candidate_ranked": right["ranked"]})
        suites = {suite: {mode: {metric:
            candidate["suites"][suite]["metrics"][mode][metric] - baseline["suites"][suite]["metrics"][mode][metric]
            for metric in METRICS} for mode in MODES} for suite in baseline["suites"]}
        runtime_changes = {field: {"baseline": baseline.get(field), "candidate": candidate.get(field)}
                           for field in ("python_version", "sqlite_version")
                           if baseline.get(field) != candidate.get(field)}
        return {"schema_version": 1, "k": baseline["k"], "dataset": baseline["dataset"],
                "baseline_backend": baseline["backend"], "candidate_backend": candidate["backend"],
                "runtime_changes": runtime_changes, "suite_deltas": suites,
                "changed_queries": changes, "unchanged_metric_queries": len(old) - len(changes),
                "note": "Deltas are candidate minus baseline. Zero means equal metrics, not necessarily equal rankings. Synthetic cases only; no significance or superiority claim. Backend runtime/settings are included for review."}
    except (KeyError, TypeError, AttributeError):
        raise ValueError("incomplete or malformed evaluation report") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in (args.baseline, args.candidate)]
        result = compare_reports(*reports)
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(serialized, encoding="utf-8")
        print(serialized, end="")
    except (OSError, ValueError) as exc:
        # Do not echo paths/content from a custom corpus or output file.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "unable to read/write JSON reports"
        print("Comparison failed: " + message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
