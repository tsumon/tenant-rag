"""检索评测：golden.jsonl → recall@k / MRR，对比 hybrid / vector / fts。

注意：默认用 FakeEmbedder（词法级向量），数字只验证管线、不代表线上效果。
线上换 OpenAIEmbedder 后重跑，混合应显著优于单通道——这就是评测集的价值：
改 tokenizer、改 RRF 参数、换 embedding 模型，先看数字再上线。

CI 门禁用法：python3 eval/run_eval.py --min-recall 0.8 --min-mrr 0.7
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tenantrag import TenantRAG
from tenantrag.embed import FakeEmbedder
from tenantrag.search import build_fts_query, rrf


def load_golden(path):
    docs, queries = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            (queries if row["type"] == "query" else docs).append(row)
    return docs, queries


def recall_at_k(expected, ranked, k):
    topk = [d for d, _ in ranked[:k]]
    return sum(1 for e in expected if e in topk) / len(expected)


def mrr(expected, ranked):
    for i, (d, _) in enumerate(ranked):
        if d in expected:
            return 1.0 / (i + 1)
    return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--min-recall", type=float, default=0.0)
    ap.add_argument("--min-mrr", type=float, default=0.0)
    args = ap.parse_args()

    golden = os.path.join(os.path.dirname(__file__), "golden.jsonl")
    docs, queries = load_golden(golden)

    tmp = tempfile.mkdtemp()
    rag = TenantRAG(path=os.path.join(tmp, "eval.db"), embedder=FakeEmbedder())
    rag.register("eval-tenant", "eval-key")
    for d in docs:
        rag.index("eval-key", d["text"], doc_id=d["doc_id"], metadata=d.get("metadata"))

    modes = ["hybrid", "vector", "fts"]
    stats = {m: {"recall": 0.0, "mrr": 0.0} for m in modes}
    for q in queries:
        qvec = rag.embedder.embed([q["query"]])[0]
        # 与 service.hybrid_search 对齐：余弦 <= 0 视为无信号
        vec_ids = [
            d for d, s in rag.store.vector_search("eval-tenant", qvec, 50) if s > 0
        ]
        fts_q = build_fts_query(q["query"])
        fts_ids = (
            [d for d, _ in rag.store.fts_search("eval-tenant", fts_q, 50)]
            if fts_q
            else []
        )
        ranked = {
            "hybrid": rrf([vec_ids, fts_ids])[: args.k],
            "vector": [(d, 0) for d in vec_ids[: args.k]],
            "fts": [(d, 0) for d in fts_ids[: args.k]],
        }
        for m in modes:
            stats[m]["recall"] += recall_at_k(q["expected"], ranked[m], args.k)
            stats[m]["mrr"] += mrr(q["expected"], ranked[m])

    n = len(queries)
    print(f"# eval: {n} queries, k={args.k} (FakeEmbedder, 词法级)")
    print("| mode | recall@k | MRR |")
    print("|------|----------|-----|")
    ok = True
    for m in modes:
        r = stats[m]["recall"] / n
        mr = stats[m]["mrr"] / n
        print(f"| {m} | {r:.3f} | {mr:.3f} |")
        if m == "hybrid":
            if r < args.min_recall:
                print(f"GATE FAIL: hybrid recall {r:.3f} < {args.min_recall}")
                ok = False
            if mr < args.min_mrr:
                print(f"GATE FAIL: hybrid mrr {mr:.3f} < {args.min_mrr}")
                ok = False
    rag.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
