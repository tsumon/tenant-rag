"""Two-stage reranking adapters: NoOp, zero-dependency LexicalCrossReranker, and HTTP APIReranker."""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from typing import Any


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")


def _extract_features(text: str) -> tuple[set[str], set[str], set[str], set[str]]:
    """Return (cjk_chars, cjk_bigrams, alnum_tokens, numeric_tokens) for fine-grained matching."""
    cjk_chars: set[str] = set()
    cjk_bigrams: set[str] = set()
    for run in _CJK_RE.findall(text):
        for ch in run:
            cjk_chars.add(ch)
        for i in range(len(run) - 1):
            cjk_bigrams.add(run[i : i + 2])
    alnum = {tok.lower() for tok in _ALNUM_RE.findall(text)}
    nums = set(_NUM_RE.findall(text))
    return cjk_chars, cjk_bigrams, alnum, nums


class BaseReranker:
    name: str = "base"

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError


class NoOpReranker(BaseReranker):
    name = "none"

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for idx, item in enumerate(candidates):
            copy = dict(item)
            copy["rerank_score"] = copy.get("score", 0.0)
            copy["stage1_rank"] = idx + 1
            copy["rerank_rank"] = idx + 1
            copy["rank_delta"] = 0
            out.append(copy)
        return out


class LexicalCrossReranker(BaseReranker):
    """Zero-dependency bilingual cross-scorer that fixes <3-char CJK and exact-code gaps."""

    name = "lexical"

    def __init__(self, stage1_weight: float = 0.6, cross_weight: float = 0.4):
        self.stage1_weight = stage1_weight
        self.cross_weight = cross_weight

    def _cross_score(self, query: str, doc_text: str) -> float:
        q_lower = query.strip().lower()
        d_lower = doc_text.lower()
        if not q_lower or not d_lower:
            return 0.0

        q_chars, q_bi, q_alnum, q_nums = _extract_features(q_lower)
        d_chars, d_bi, d_alnum, d_nums = _extract_features(d_lower)

        # CJK coverage (covers 1-char & 2-char Chinese words missed by SQLite trigram!)
        char_cov = (len(q_chars & d_chars) / len(q_chars)) if q_chars else 0.0
        bi_cov = (len(q_bi & d_bi) / len(q_bi)) if q_bi else char_cov
        alnum_cov = (len(q_alnum & d_alnum) / len(q_alnum)) if q_alnum else 0.0
        num_cov = (len(q_nums & d_nums) / len(q_nums)) if q_nums else 0.0

        active_parts: list[tuple[float, float]] = []
        if q_chars:
            active_parts.append((0.55 * bi_cov + 0.45 * char_cov, 1.0))
        if q_alnum:
            active_parts.append((alnum_cov, 1.0))
        if q_nums:
            active_parts.append((num_cov, 0.8))

        if not active_parts:
            return 0.0
        base = sum(val * w for val, w in active_parts) / sum(w for _, w in active_parts)

        # Ordered substring phrase bonus
        phrase_bonus = 0.0
        if len(q_lower) >= 2 and q_lower in d_lower:
            phrase_bonus = 0.25
        else:
            # Check meaningful sub-clauses (length >= 2)
            clauses = [c.strip() for c in re.split(r"[\s,，。？！?!:：;；]+", q_lower) if len(c.strip()) >= 2]
            if clauses:
                hits = sum(1 for c in clauses if c in d_lower)
                phrase_bonus = 0.18 * (hits / len(clauses))

        return min(1.0, base + phrase_bonus)

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        raw_scores = [float(c.get("score", 0.0)) for c in candidates]
        max_s = max(raw_scores)
        min_s = min(raw_scores)
        span = (max_s - min_s) if max_s > min_s else 1.0

        scored: list[dict[str, Any]] = []
        for idx, item in enumerate(candidates):
            norm_s1 = (float(item.get("score", 0.0)) - min_s) / span if max_s > min_s else (1.0 / (idx + 1))
            cross = self._cross_score(query, item.get("text", ""))
            final = self.stage1_weight * norm_s1 + self.cross_weight * cross
            copy = dict(item)
            copy["stage1_rank"] = idx + 1
            copy["stage1_score"] = round(float(item.get("score", 0.0)), 6)
            copy["cross_score"] = round(cross, 6)
            copy["rerank_score"] = round(final, 6)
            copy["score"] = round(final, 6)
            scored.append(copy)

        scored.sort(key=lambda x: (-x["rerank_score"], x["stage1_rank"]))
        for new_idx, item in enumerate(scored):
            item["rerank_rank"] = new_idx + 1
            item["rank_delta"] = item["stage1_rank"] - item["rerank_rank"]
        return scored


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        response.close()
        raise RuntimeError("rerank endpoint redirected; configure final URL directly")


class APIReranker(BaseReranker):
    """OpenAI / Cohere / Jina compatible /rerank HTTP endpoint adapter."""

    name = "api"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 15.0):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("rerank base_url must be HTTP(S)")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        url = self.base_url if self.base_url.endswith("/rerank") else f"{self.base_url}/rerank"
        payload = {
            "model": self.model,
            "query": query,
            "documents": [c.get("text", "") for c in candidates],
            "top_n": len(candidates),
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read(8 * 1024 * 1024).decode("utf-8"))
        results = data.get("results") or data.get("data") or []
        score_map: dict[int, float] = {}
        for item in results:
            idx = item.get("index")
            rel = item.get("relevance_score", item.get("score"))
            if isinstance(idx, int) and 0 <= idx < len(candidates) and isinstance(rel, (int, float)) and math.isfinite(rel):
                score_map[idx] = float(rel)

        out: list[dict[str, Any]] = []
        for idx, item in enumerate(candidates):
            copy = dict(item)
            copy["stage1_rank"] = idx + 1
            copy["stage1_score"] = round(float(item.get("score", 0.0)), 6)
            rscore = score_map.get(idx, 0.0)
            copy["cross_score"] = round(rscore, 6)
            copy["rerank_score"] = round(rscore, 6)
            copy["score"] = round(rscore, 6)
            out.append(copy)
        out.sort(key=lambda x: (-x["rerank_score"], x["stage1_rank"]))
        for new_idx, item in enumerate(out):
            item["rerank_rank"] = new_idx + 1
            item["rank_delta"] = item["stage1_rank"] - item["rerank_rank"]
        return out
