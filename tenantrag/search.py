"""混合检索：向量（语义/RAG）+ FTS5（关键词/grep）→ RRF 融合。

为什么混合：纯向量对精确词（订单号、人名、金额）经常翻车，纯关键词对
同义改写翻车。两路召回 + RRF 融合是生产里最稳的 baseline，比单路强，
比 learned ranker 便宜。
"""
from __future__ import annotations

import re

from .store import Store

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")
_CJK_RUN_RE = re.compile(r"^[\u4e00-\u9fff]+$")


def _cjk_trigrams(run: str) -> list[str]:
    """CJK run 切滑动 trigram：query 跑 "腾讯视频多少钱" 时，
    doc 里只有 "腾讯视频" 也能命中（trigram 索引的正确用法）。
    长度 <3 的 run 交给向量通道（trigram 查不了 2 字词）。"""
    if len(run) < 3:
        return []
    return [run[i : i + 3] for i in range(len(run) - 2)]


def build_fts_query(text: str) -> str | None:
    """把用户 query 转成安全的 FTS5 MATCH 表达式。

    直接拼用户输入进 MATCH 会触发 FTS5 语法错误（引号/括号/AND 等），
    所以只抽 token：字母数字按词、CJK 按滑动 trigram，每个加双引号
    转义后 OR 连接。无 token 返回 None。
    """
    terms: list[str] = []
    for tok in _TOKEN_RE.findall(text):
        if _CJK_RUN_RE.match(tok):
            terms.extend(_cjk_trigrams(tok))
        else:
            terms.append(tok)
    if not terms:
        return None
    seen = dict.fromkeys(terms)  # 去重保序
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in seen)


def rrf(rank_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：score = Σ 1/(k+rank)。免训练、鲁棒。"""
    scores: dict[str, float] = {}
    for lst in rank_lists:
        for rank, doc_id in enumerate(lst):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def hybrid_search(
    store: Store,
    tenant_id: str,
    query_text: str,
    query_vec: list[float],
    k: int = 10,
    vec_k: int = 50,
    fts_k: int = 50,
    rrf_k: int = 60,
) -> list[dict]:
    # 非法 k 直接报错（int(None)/int("x") 会抛 TypeError/ValueError），
    # 合法数值统一转 int：k=2.5 切片会 TypeError，k<=0 切片语义诡异（fused[:-1]）。
    k = max(1, int(k))
    vec_hits = [
        (d, s) for d, s in store.vector_search(tenant_id, query_vec, vec_k) if s > 0
    ]
    # 余弦 <= 0 视为无信号：FakeEmbedder 下无词交集得 0，真实模型下负分表示语义相反。
    # 不过滤的话，任何查询都会把租户全量文档带回来（top-k 填满）。
    fts_q = build_fts_query(query_text)
    fts_hits = store.fts_search(tenant_id, fts_q, fts_k) if fts_q else []

    vec_ids = [d for d, _ in vec_hits]
    fts_ids = [d for d, _ in fts_hits]
    fused = rrf([vec_ids, fts_ids], k=rrf_k)[:k]

    vec_set, fts_set = set(vec_ids), set(fts_ids)
    docs = store.fetch_docs(tenant_id, [d for d, _ in fused])
    results = []
    for doc_id, score in fused:
        doc = docs.get(doc_id)
        if doc is None:
            continue  # 极端竞态下索引与文档不一致时丢弃，不崩
        matched = []
        if doc_id in vec_set:
            matched.append("vector")
        if doc_id in fts_set:
            matched.append("fts")
        results.append({**doc, "score": round(score, 6), "matched": matched})
    return results
