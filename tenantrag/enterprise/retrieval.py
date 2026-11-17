"""Enterprise pre-retrieval Metadata DSL + ACL filtering, hybrid fusion, reranking, context expansion, and abstention gate."""
from __future__ import annotations

import json
import math
import time
from typing import Any

from tenantrag.search import build_fts_query, rrf, score_fusion, validate_fusion

from .rerank import BaseReranker, LexicalCrossReranker, NoOpReranker
from .tenant_pool import TenantNode, _unit_vec


SUPPORTED_OPERATORS = frozenset(
    {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$contains", "$exists"}
)


def validate_filters(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    if filters is None:
        return None
    if not isinstance(filters, dict):
        raise ValueError("filters must be a JSON object")
    for field, cond in filters.items():
        if not isinstance(field, str) or not field.strip():
            raise ValueError("filter field names must be non-empty strings")
        if isinstance(cond, dict):
            if not cond:
                raise ValueError(f"filter operator object for {field!r} cannot be empty")
            for op, val in cond.items():
                if op not in SUPPORTED_OPERATORS:
                    raise ValueError(f"unsupported filter operator {op!r}")
                if op in ("$in", "$nin") and not isinstance(val, list):
                    raise ValueError(f"operator {op!r} requires a list value")
                if op == "$exists" and type(val) is not bool:
                    raise ValueError("operator '$exists' requires a boolean value")
    return filters


def _extract_field(metadata: dict[str, Any], path: str) -> tuple[bool, Any]:
    cur: Any = metadata
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def _num_or_cmp(a: Any, b: Any) -> bool:
    return type(a) in (int, float) and type(b) in (int, float) or (isinstance(a, str) and isinstance(b, str))


def evaluate_filter(metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True
    for field, cond in filters.items():
        exists, val = _extract_field(metadata, field)
        if not isinstance(cond, dict):
            if not exists or val != cond:
                return False
            continue
        for op, target in cond.items():
            if op == "$exists":
                if exists != target:
                    return False
                continue
            if op == "$eq":
                if not exists or val != target:
                    return False
            elif op == "$ne":
                if exists and val == target:
                    return False
            elif op == "$gt":
                if not exists or not _num_or_cmp(val, target) or not (val > target):
                    return False
            elif op == "$gte":
                if not exists or not _num_or_cmp(val, target) or not (val >= target):
                    return False
            elif op == "$lt":
                if not exists or not _num_or_cmp(val, target) or not (val < target):
                    return False
            elif op == "$lte":
                if not exists or not _num_or_cmp(val, target) or not (val <= target):
                    return False
            elif op == "$in":
                if not exists:
                    return False
                if isinstance(val, list):
                    if not any(v in target for v in val):
                        return False
                elif val not in target:
                    return False
            elif op == "$nin":
                if exists:
                    if isinstance(val, list) and any(v in target for v in val):
                        return False
                    if val in target:
                        return False
            elif op == "$contains":
                if not exists:
                    return False
                if isinstance(val, list):
                    if target not in val:
                        return False
                elif isinstance(val, str):
                    if str(target).lower() not in val.lower():
                        return False
                else:
                    return False
    return True


def evaluate_acl(metadata: dict[str, Any], principal: dict[str, Any] | None) -> bool:
    """Enforce document-level _acl visibility before candidate selection."""
    acl = metadata.get("_acl")
    if not isinstance(acl, dict):
        return True
    visibility = acl.get("visibility", "public")
    if visibility == "public":
        return True
    if not isinstance(principal, dict):
        return False
    if principal.get("is_admin") is True:
        return True
    allow_users = acl.get("allow_users") or []
    allow_roles = acl.get("allow_roles") or []
    user_id = principal.get("user_id")
    roles = principal.get("roles") or []
    if user_id and user_id in allow_users:
        return True
    if roles and set(roles) & set(allow_roles):
        return True
    return False


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _expand_sibling_context(
    con,
    tenant_id: str,
    hits: list[dict[str, Any]],
    context_window: int,
) -> None:
    if context_window <= 0 or not hits:
        return
    parent_cache: dict[str, list[tuple[str, int, int, str, int]]] = {}
    for hit in hits:
        chunk_meta = (hit.get("metadata") or {}).get("_chunk")
        if not isinstance(chunk_meta, dict):
            hit["expanded_text"] = hit["text"]
            hit["expanded_start"] = 0
            hit["expanded_end"] = len(hit["text"])
            continue
        parent_id = chunk_meta.get("parent_id")
        cur_idx = chunk_meta.get("index")
        if not parent_id or type(cur_idx) is not int:
            hit["expanded_text"] = hit["text"]
            continue
        if parent_id not in parent_cache:
            rows = con.execute(
                "SELECT c.doc_id, c.start, c.end, d.text, d.metadata "
                "FROM doc_chunks c JOIN docs d ON c.tenant_id=d.tenant_id AND c.doc_id=d.doc_id "
                "WHERE c.tenant_id=? AND c.parent_id=? ORDER BY c.start ASC",
                (tenant_id, parent_id),
            ).fetchall()
            siblings = []
            for seq, r in enumerate(rows):
                rm = json.loads(r[4] or "{}")
                idx_val = rm.get("_chunk", {}).get("index", seq)
                siblings.append((r[0], r[1], r[2], r[3], idx_val))
            parent_cache[parent_id] = siblings
        siblings = parent_cache[parent_id]
        lo = max(0, cur_idx - context_window)
        hi = cur_idx + context_window
        window_chunks = [s for s in siblings if lo <= s[4] <= hi]
        if not window_chunks:
            hit["expanded_text"] = hit["text"]
            continue
        # Fetch parent full text to reconstruct exact slice without duplicate overlap
        p_row = con.execute(
            "SELECT text, source FROM parent_docs WHERE tenant_id=? AND parent_id=?",
            (tenant_id, parent_id),
        ).fetchone()
        exp_start = window_chunks[0][1]
        exp_end = window_chunks[-1][2]
        if p_row is not None:
            hit["expanded_text"] = p_row[0][exp_start:exp_end]
            hit["parent_source"] = p_row[1]
        else:
            hit["expanded_text"] = "\n".join(w[3] for w in window_chunks)
        hit["expanded_start"] = exp_start
        hit["expanded_end"] = exp_end


def enterprise_search(
    node: TenantNode,
    query_text: str,
    *,
    k: int = 10,
    vec_k: int = 50,
    fts_k: int = 50,
    rrf_k: int = 60,
    fusion: str = "rrf",
    lexical_weight: float | None = None,
    filters: dict[str, Any] | None = None,
    principal: dict[str, Any] | None = None,
    reranker: BaseReranker | None = None,
    context_window: int = 0,
    dedupe_parent: bool = False,
    check_abstention: bool = True,
    min_vector_score: float = 0.28,
    explain: bool = False,
) -> dict[str, Any]:
    if not isinstance(query_text, str) or not query_text.strip():
        raise ValueError("query must be a non-empty string")
    validate_filters(filters)
    weight = validate_fusion(fusion, lexical_weight)
    k = max(1, min(int(k), 100))
    context_window = max(0, min(int(context_window), 5))
    if type(min_vector_score) not in (int, float) or not math.isfinite(min_vector_score):
        raise ValueError("min_vector_score must be a finite number")

    t0 = time.perf_counter()
    model, expected = node.rag.identity.capture()
    node.rag.identity.check(expected=expected)
    vectors = model.embed([query_text])
    dim = node.rag.identity.validate(vectors, 1)
    node.rag.identity.check(dim, expected=expected)
    q_unit = _unit_vec(vectors[0])
    t_embed = (time.perf_counter() - t0) * 1000.0

    # Stage 1a: Pre-retrieval Metadata + ACL filtering on cached snapshot
    t1 = time.perf_counter()
    snapshot_items, _ = node.get_vector_snapshot()
    allowed_vecs: list[tuple[str, list[float] | None]] = []
    allowed_ids: set[str] = set()
    filtered_out_acl = 0
    filtered_out_meta = 0
    for doc_id, unit_v, meta in snapshot_items:
        if not evaluate_acl(meta, principal):
            filtered_out_acl += 1
            continue
        if not evaluate_filter(meta, filters):
            filtered_out_meta += 1
            continue
        allowed_ids.add(doc_id)
        allowed_vecs.append((doc_id, unit_v))
    t_filter = (time.perf_counter() - t1) * 1000.0

    # Stage 1b: Vector search restricted to pre-filtered allowed_ids
    t2 = time.perf_counter()
    vec_scored: list[tuple[str, float]] = []
    if q_unit is not None:
        for doc_id, unit_v in allowed_vecs:
            if unit_v is None or len(unit_v) != len(q_unit):
                continue
            sim = _dot(q_unit, unit_v)
            if sim > 0.0:
                vec_scored.append((doc_id, sim))
    vec_scored.sort(key=lambda x: x[1], reverse=True)
    vec_hits = vec_scored[:vec_k]
    t_vector = (time.perf_counter() - t2) * 1000.0

    # Stage 1c: FTS5 trigram search filtered against allowed_ids BEFORE slicing fts_k
    t3 = time.perf_counter()
    fts_q = None if (fusion == "score" and weight == 0) else build_fts_query(query_text)
    fts_hits: list[tuple[str, float]] = []
    if fts_q and allowed_ids:
        with node.read_connection() as con:
            fetch_limit = max(fts_k * 4, 200)
            rows = con.execute(
                "SELECT doc_id, bm25(docs_fts) AS b FROM docs_fts "
                "WHERE docs_fts MATCH ? AND tenant_id = ? ORDER BY b LIMIT ?",
                (fts_q, node.tenant_id, fetch_limit),
            ).fetchall()
        for did, bm25_val in rows:
            if did in allowed_ids:
                fts_hits.append((did, float(bm25_val)))
                if len(fts_hits) >= fts_k:
                    break
    t_fts = (time.perf_counter() - t3) * 1000.0

    # Stage 1d: Fusion
    t4 = time.perf_counter()
    vec_ids = [d for d, _ in vec_hits]
    fts_ids = [d for d, _ in fts_hits]
    vec_score_map = dict(vec_hits)
    fts_bm25_map = dict(fts_hits)

    fused = (
        rrf([vec_ids, fts_ids], k=rrf_k)
        if fusion == "rrf"
        else score_fusion(vec_hits, fts_ids, weight)
    )
    # Keep deeper candidate pool for reranker before final top-k cut
    rerank_pool_size = max(k * 3, 25) if reranker and reranker.name != "none" else k
    fused_pool = fused[:rerank_pool_size]
    t_fusion = (time.perf_counter() - t4) * 1000.0

    # Fetch documents for candidate pool
    pool_ids = [d for d, _ in fused_pool]
    docs_map: dict[str, dict[str, Any]] = {}
    if pool_ids:
        placeholders = ",".join("?" for _ in pool_ids)
        with node.read_connection() as con:
            rows = con.execute(
                f"SELECT doc_id, text, metadata FROM docs WHERE tenant_id=? AND doc_id IN ({placeholders})",
                (node.tenant_id, *pool_ids),
            ).fetchall()
        docs_map = {
            r[0]: {"doc_id": r[0], "text": r[1], "metadata": json.loads(r[2] or "{}")}
            for r in rows
        }

    vec_set, fts_set = set(vec_ids), set(fts_ids)
    candidates: list[dict[str, Any]] = []
    for doc_id, fscore in fused_pool:
        doc = docs_map.get(doc_id)
        if doc is None:
            continue
        matched = []
        if doc_id in vec_set:
            matched.append("vector")
        if doc_id in fts_set:
            matched.append("fts")
        candidates.append(
            {
                **doc,
                "score": round(float(fscore), 6),
                "vector_score": round(vec_score_map[doc_id], 6) if doc_id in vec_score_map else None,
                "fts_bm25": round(fts_bm25_map[doc_id], 6) if doc_id in fts_bm25_map else None,
                "matched": matched,
            }
        )

    # Stage 2: Rerank
    t5 = time.perf_counter()
    active_reranker = reranker or NoOpReranker()
    reranked = active_reranker.rerank(query_text, candidates)
    if dedupe_parent:
        seen_parents: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for hit in reranked:
            pid = (hit.get("metadata") or {}).get("_chunk", {}).get("parent_id") or hit["doc_id"]
            if pid not in seen_parents:
                seen_parents.add(pid)
                deduped.append(hit)
        reranked = deduped
    final_hits = reranked[:k]
    t_rerank = (time.perf_counter() - t5) * 1000.0

    # Stage 3: Parent-Child Sibling Context Expansion
    t6 = time.perf_counter()
    if context_window > 0 and final_hits:
        with node.read_connection() as con:
            _expand_sibling_context(con, node.tenant_id, final_hits, context_window)
    t_expand = (time.perf_counter() - t6) * 1000.0

    # Stage 4: Evidence Sufficiency / Abstention Gate
    top_vec = max((h.get("vector_score") or 0.0 for h in final_hits), default=0.0)
    has_fts = any("fts" in h.get("matched", []) for h in final_hits)
    has_strong_cross = any((h.get("cross_score") or 0.0) >= 0.45 for h in final_hits)
    evidence_sufficient = True
    abstention_reason = None
    if check_abstention:
        if not final_hits:
            evidence_sufficient = False
            abstention_reason = "no_matching_documents_after_acl_and_filters"
        elif top_vec < float(min_vector_score) and not has_fts and not has_strong_cross:
            evidence_sufficient = False
            abstention_reason = (
                f"insufficient_evidence (max_vector_score={top_vec:.3f} < threshold={min_vector_score:.3f}, "
                "no keyword/cross-lexical anchor)"
            )

    total_ms = (time.perf_counter() - t0) * 1000.0
    result: dict[str, Any] = {
        "hits": final_hits,
        "total_candidates": len(allowed_ids),
        "evidence_sufficient": evidence_sufficient,
        "abstention_reason": abstention_reason,
        "latency_ms": round(total_ms, 2),
        "reranker": active_reranker.name,
        "fusion": fusion,
    }
    if explain:
        result["diagnostics"] = {
            "timings_ms": {
                "embed": round(t_embed, 2),
                "filter_acl": round(t_filter, 2),
                "vector": round(t_vector, 2),
                "fts": round(t_fts, 2),
                "fusion": round(t_fusion, 2),
                "rerank": round(t_rerank, 2),
                "context_expand": round(t_expand, 2),
                "total": round(total_ms, 2),
            },
            "fts_query": fts_q,
            "corpus_chunks": len(snapshot_items),
            "allowed_candidates": len(allowed_ids),
            "filtered_out_acl": filtered_out_acl,
            "filtered_out_metadata": filtered_out_meta,
            "vector_top": [{"doc_id": d, "cosine": round(s, 4)} for d, s in vec_hits[:5]],
            "fts_top": [{"doc_id": d, "bm25": round(b, 4)} for d, b in fts_hits[:5]],
        }
    return result
