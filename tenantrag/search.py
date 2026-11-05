"""混合检索：向量（语义/RAG）+ FTS5（关键词/grep）→ RRF 融合。

为什么混合：纯向量对精确词（订单号、人名、金额）经常翻车，纯关键词对
同义改写翻车。两路召回可互补，但跨语言和弱关键词命中也可能降低排名质量。
保留默认 RRF；可显式选择保留余弦分差的 score 融合，并按模型/语料评测。
"""
from __future__ import annotations

import re
import math

from .store import Store

DEFAULT_LEXICAL_WEIGHT = 0.2


def validate_fusion(fusion, lexical_weight=None):
    """Validate before embedding/provider IO; weights are cosine-score units."""
    if not isinstance(fusion, str) or fusion not in ('rrf', 'score'):
        raise ValueError("fusion must be 'rrf' or 'score'")
    if fusion == 'rrf':
        if lexical_weight is not None:
            raise ValueError("lexical_weight requires fusion='score'")
        return None
    if lexical_weight is None:
        return DEFAULT_LEXICAL_WEIGHT
    if type(lexical_weight) not in (int, float):
        raise ValueError('lexical_weight must be a finite number between 0 and 1')
    try:
        weight = float(lexical_weight)
    except OverflowError:
        raise ValueError('lexical_weight must be a finite number between 0 and 1') from None
    if not math.isfinite(weight) or not 0 <= weight <= 1:
        raise ValueError('lexical_weight must be a finite number between 0 and 1')
    return weight


def score_fusion(vector_hits, fts_ids, lexical_weight=DEFAULT_LEXICAL_WEIGHT):
    """Positive cosine + weight / keyword rank, with a bounded lexical bonus.

    Cosine margins remain visible. A keyword-only document starts at zero.
    Scores are ranking signals, not probabilities. Stable ties prefer the
    earlier vector rank, then keyword rank. Weight zero is vector-only.
    This does not solve numeric filters or model calibration in general.
    """
    weight = validate_fusion('score', lexical_weight)
    scores = {doc_id: score for doc_id, score in vector_hits if score > 0}
    if weight:
        for rank, doc_id in enumerate(dict.fromkeys(fts_ids), 1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / rank
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)

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
    *,
    fusion: str = 'rrf',
    lexical_weight: float | None = None,
) -> list[dict]:
    weight = validate_fusion(fusion, lexical_weight)
    # 非法 k 直接报错（int(None)/int("x") 会抛 TypeError/ValueError），
    # 合法数值统一转 int：k=2.5 切片会 TypeError，k<=0 切片语义诡异（fused[:-1]）。
    k = max(1, int(k))
    vec_hits = [
        (d, s) for d, s in store.vector_search(tenant_id, query_vec, vec_k) if s > 0
    ]
    # 余弦 <= 0 延续既有无信号过滤；负分并不能普遍解释为“语义相反”。
    # 不过滤的话，任何查询都会把租户全量文档带回来（top-k 填满）。
    fts_q = None if fusion == 'score' and weight == 0 else build_fts_query(query_text)
    fts_hits = store.fts_search(tenant_id, fts_q, fts_k) if fts_q else []

    vec_ids = [d for d, _ in vec_hits]
    fts_ids = [d for d, _ in fts_hits]
    fused = (rrf([vec_ids, fts_ids], k=rrf_k) if fusion == 'rrf'
             else score_fusion(vec_hits, fts_ids, weight))[:k]

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
