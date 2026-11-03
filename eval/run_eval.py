"""Reproducible retrieval checks, not a claim of production semantic quality.

The default hash-bag embedder runs offline. Pipeline regressions and synthetic
semantic challenges are reported separately; only the pipeline suite is gated
by default. Real provider calls require both --embedder openai and
--allow-provider-requests. No hosted requests are part of the test suite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tenantrag import TenantRAG
from tenantrag.embed import FakeEmbedder, OpenAIEmbedder
from tenantrag.search import build_fts_query, hybrid_search

MODES = ("hybrid", "vector", "fts")
SUITES = ("pipeline", "semantic")
CANDIDATES = 50
DEFAULT_GOLDEN = Path(__file__).with_name("golden.jsonl")


def load_golden(path):
    """Validate labels before computing any metrics or making provider calls."""
    docs, queries = [], []
    doc_ids, query_ids = set(), set()
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"golden line {line_number}: invalid JSON") from exc
            if not isinstance(row, dict) or row.get("type") not in ("doc", "query"):
                raise ValueError(f"golden line {line_number}: type must be doc or query")
            if row["type"] == "doc":
                doc_id = row.get("doc_id")
                if not isinstance(doc_id, str) or not doc_id.strip() or doc_id in doc_ids:
                    raise ValueError(f"golden line {line_number}: doc_id must be non-empty and unique")
                if not isinstance(row.get("text"), str) or not row["text"].strip():
                    raise ValueError(f"golden line {line_number}: document text must be non-empty")
                if not isinstance(row.get("metadata", {}), dict):
                    raise ValueError(f"golden line {line_number}: metadata must be an object")
                doc_ids.add(doc_id)
                docs.append(row)
            else:
                query_id = row.get("id", f"q{len(queries) + 1:03d}")
                if not isinstance(query_id, str) or not query_id.strip() or query_id in query_ids:
                    raise ValueError(f"golden line {line_number}: query id must be non-empty and unique")
                if not isinstance(row.get("query"), str) or not row["query"].strip():
                    raise ValueError(f"golden line {line_number}: query must be non-empty")
                expected = row.get("expected")
                if (not isinstance(expected, list) or not expected
                        or any(not isinstance(doc_id, str) or not doc_id for doc_id in expected)
                        or len(set(expected)) != len(expected)):
                    raise ValueError(f"golden line {line_number}: expected must contain unique document IDs")
                suite = row.get("suite", "pipeline")
                if suite not in SUITES:
                    raise ValueError(f"golden line {line_number}: suite must be pipeline or semantic")
                tags = row.get("tags", [])
                if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag for tag in tags):
                    raise ValueError(f"golden line {line_number}: tags must be a list of non-empty strings")
                query_ids.add(query_id)
                queries.append({**row, "id": query_id, "suite": suite})
    if not docs or not queries:
        raise ValueError("golden dataset must contain documents and queries")
    for query in queries:
        if not set(query["expected"]) <= doc_ids:
            raise ValueError(f"query {query['id']}: expected references an unknown document")
    return docs, queries


def recall_at_k(expected, ranked, k):
    topk = {doc_id for doc_id, _ in ranked[:k]}
    return len(set(expected) & topk) / len(set(expected))


def mrr(expected, ranked):
    for index, (doc_id, _) in enumerate(ranked, 1):
        if doc_id in expected:
            return 1.0 / index
    return 0.0


def evaluate(docs, queries, embedder, k=3):
    """Index a fresh temporary corpus and compare the real hybrid search path.

    Results contain no wall-clock timings or timestamps, allowing deterministic
    offline diffs. MRR is truncated at k, just like recall@k. Retrieval uses the
    same 50 candidates per channel and positive-cosine rule as the service.
    """
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer")
    if not docs or not queries:
        raise ValueError("evaluation requires documents and queries")
    details = []
    with tempfile.TemporaryDirectory(prefix="tenant-rag-eval-") as directory:
        rag = TenantRAG(path=os.path.join(directory, "eval.db"), embedder=embedder)
        try:
            rag.register("eval-tenant", "eval-test-key")
            for doc in docs:
                rag.index("eval-test-key", doc["text"], doc_id=doc["doc_id"],
                          metadata=doc.get("metadata"))
            for query in queries:
                qvec = embedder.embed([query["query"]])[0]
                vector = [
                    (doc_id, score) for doc_id, score in
                    rag.store.vector_search("eval-tenant", qvec, CANDIDATES) if score > 0
                ][:k]
                fts_query = build_fts_query(query["query"])
                fts = (rag.store.fts_search("eval-tenant", fts_query, CANDIDATES)
                       if fts_query else [])[:k]
                hybrid = [(hit["doc_id"], hit["score"]) for hit in hybrid_search(
                    rag.store, "eval-tenant", query["query"], qvec, k=k)]
                ranked = {"hybrid": hybrid, "vector": vector, "fts": fts}
                details.append({
                    "id": query["id"], "suite": query["suite"],
                    "query": query["query"], "expected": query["expected"],
                    "tags": query.get("tags", []),
                    "ranked": {mode: [doc_id for doc_id, _ in ranked[mode]] for mode in MODES},
                    "metrics": {mode: {
                        "recall_at_k": recall_at_k(query["expected"], ranked[mode], k),
                        "mrr_at_k": mrr(query["expected"], ranked[mode]),
                    } for mode in MODES},
                })
        finally:
            rag.close()
    suites = {}
    for suite in SUITES:
        selected = [query for query in details if query["suite"] == suite]
        if not selected:
            continue
        suites[suite] = {
            "queries": len(selected),
            "metrics": {mode: {
                metric: sum(query["metrics"][mode][metric] for query in selected) / len(selected)
                for metric in ("recall_at_k", "mrr_at_k")
            } for mode in MODES},
        }
    return {"k": k, "candidates_per_channel": CANDIDATES, "suites": suites, "queries": details}


def _positive_int(value):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _threshold(value):
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite number between 0 and 1") from exc
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be a finite number between 0 and 1")
    return parsed


def _print_report(report):
    backend = report["backend"]
    print(f"Evaluation: {report['dataset']['documents']} documents, k={report['k']}, "
          f"embedder={backend['name']}, model={backend['model']}")
    print("Semantic quality: " + report["semantic_quality"])
    print("Synthetic fixtures only. No production-quality or hybrid-superiority claim.")
    for name, suite in report["suites"].items():
        label = "offline diagnostic only" if name == "semantic" and backend["name"] == "fake" else "measured"
        print(f"\n{name}: {suite['queries']} queries ({label})")
        print(f"mode       recall@{report['k']}    MRR@{report['k']}")
        for mode, metrics in suite["metrics"].items():
            print(f"{mode:<10} {metrics['recall_at_k']:.3f}        {metrics['mrr_at_k']:.3f}")
    gate = report["gate"]
    print(f"\nGATE {'PASS' if gate['passed'] else 'FAIL'}: {gate['suite']} / hybrid "
          f"(recall >= {gate['min_recall']}, MRR@k >= {gate['min_mrr']})")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--k", type=_positive_int, default=3)
    parser.add_argument("--min-recall", type=_threshold, default=0.0)
    parser.add_argument("--min-mrr", type=_threshold, default=0.0)
    parser.add_argument("--suite", choices=("all",) + SUITES, default="all")
    parser.add_argument("--gate-suite", choices=SUITES, default="pipeline")
    parser.add_argument("--embedder", choices=("fake", "openai"), default="fake")
    parser.add_argument("--allow-provider-requests", action="store_true",
                        help="Permit sending all selected fixture texts to the provider; may incur charges")
    parser.add_argument("--base-url", default=os.environ.get("EMBED_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--model", default=os.environ.get("EMBED_MODEL", "text-embedding-3-small"))
    parser.add_argument("--json", action="store_true", help="Print the complete JSON report")
    parser.add_argument("--output", type=Path, help="Also save the JSON report to this file")
    args = parser.parse_args(argv)
    if args.embedder == "openai" and not args.allow_provider_requests:
        parser.error("provider requests require --allow-provider-requests (fixture text is sent and charges may apply)")
    if args.embedder == "fake" and args.gate_suite == "semantic":
        parser.error("semantic gates require a real provider; fake semantic scores are diagnostics only")
    try:
        docs, queries = load_golden(args.golden)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.suite != "all":
        queries = [query for query in queries if query["suite"] == args.suite]
    if not queries or not any(query["suite"] == args.gate_suite for query in queries):
        parser.error("selected queries must contain the requested gate suite")
    if args.embedder == "openai":
        api_key = os.environ.get("EMBED_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key or not api_key.strip():
            parser.error("set EMBED_API_KEY (or OPENAI_API_KEY) before provider evaluation")
        try:
            embedder = OpenAIEmbedder(api_key=api_key, base_url=args.base_url, model=args.model)
        except ValueError as exc:
            parser.error(str(exc))
        print("Provider evaluation enabled: fixture documents and queries will be sent; charges may apply.", file=sys.stderr)
    else:
        embedder = FakeEmbedder()
    try:
        report = evaluate(docs, queries, embedder, args.k)
    except Exception as exc:
        # Provider error messages/bodies can contain credentials or input. The
        # exception class is sufficient here; callers can debug in their own app.
        print(f"Evaluation failed ({type(exc).__name__}); no result or gate pass produced.", file=sys.stderr)
        return 2
    report.update({
        "schema_version": 1,
        "dataset": {"name": args.golden.name,
                    "sha256": hashlib.sha256(args.golden.read_bytes()).hexdigest(),
                    "documents": len(docs), "queries": len(queries)},
        "backend": {"name": args.embedder,
                    "model": args.model if args.embedder == "openai" else "hash-bow-v1",
                    "dimensions": embedder.dim},
        "sqlite_version": sqlite3.sqlite_version,
        "semantic_quality": (
            "unverified (offline lexical embedder)" if args.embedder == "fake" else
            "unverified (semantic suite not evaluated)" if "semantic" not in report["suites"] else
            "measured on synthetic cases only; production quality unverified"
        ),
    })
    gate_metrics = report["suites"][args.gate_suite]["metrics"]["hybrid"]
    passed = (gate_metrics["recall_at_k"] >= args.min_recall
              and gate_metrics["mrr_at_k"] >= args.min_mrr)
    report["gate"] = {"suite": args.gate_suite, "mode": "hybrid", "min_recall": args.min_recall,
                      "min_mrr": args.min_mrr, "passed": passed}
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        try:
            args.output.write_text(serialized, encoding="utf-8")
        except OSError:
            print("Unable to write the requested evaluation report.", file=sys.stderr)
            return 2
    if args.json:
        print(serialized, end="")
    else:
        _print_report(report)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
