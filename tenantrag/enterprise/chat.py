"""Grounded RAG Q&A engine with abstention gating, inline [n] citations, and OpenAI SSE streaming."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Iterator
import urllib.parse
import urllib.request


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？\n])\s*|(?<=[.!?])\s+")
_TOKEN_EXTRACT = re.compile(r"[\u4e00-\u9fff]{1,2}|[A-Za-z0-9_.-]+")


def build_citations(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for idx, hit in enumerate(hits, 1):
        meta = hit.get("metadata") or {}
        chunk_meta = meta.get("_chunk") or {}
        parent_id = chunk_meta.get("parent_id") or hit["doc_id"]
        source = chunk_meta.get("source") or hit.get("parent_source") or meta.get("source") or parent_id
        start = hit.get("expanded_start", chunk_meta.get("start", 0))
        end = hit.get("expanded_end", chunk_meta.get("end", len(hit.get("text", ""))))
        excerpt = hit.get("expanded_text") or hit.get("text", "")
        citations.append(
            {
                "index": idx,
                "doc_id": hit["doc_id"],
                "parent_id": parent_id,
                "source": source,
                "start": start,
                "end": end,
                "score": hit.get("score", 0.0),
                "vector_score": hit.get("vector_score"),
                "matched": hit.get("matched", []),
                "quote": excerpt[:420] + ("..." if len(excerpt) > 420 else ""),
            }
        )
    return citations


def _extractive_grounded_answer(query: str, hits: list[dict[str, Any]], citations: list[dict[str, Any]]) -> str:
    """Zero-key deterministic grounded synthesis from retrieved chunks with inline [n] citations."""
    q_tokens = {t.lower() for t in _TOKEN_EXTRACT.findall(query)}
    selected_lines: list[str] = []
    for idx, hit in enumerate(hits[:4], 1):
        body = (hit.get("expanded_text") or hit.get("text") or "").strip()
        # Pick up to 2 most query-relevant sentences from this hit
        raw_sents = [s.strip() for s in _SENTENCE_SPLIT.split(body) if len(s.strip()) >= 4]
        if not raw_sents:
            raw_sents = [body[:220]]
        scored_sents = []
        for s in raw_sents:
            s_low = s.lower()
            overlap = sum(1 for tok in q_tokens if tok in s_low)
            scored_sents.append((overlap, len(s), s))
        scored_sents.sort(key=lambda x: (x[0], -abs(x[1] - 80)), reverse=True)
        best = scored_sents[0][2]
        if len(best) > 220:
            best = best[:217].rstrip() + "..."
        src = citations[idx - 1]["source"]
        selected_lines.append(f"- {best} [{idx}]（来源：`{src}`）")

    header = "根据当前租户知识库中已校验通过的证据片段，整理答复如下：\n\n"
    footer = "\n\n> 模式说明：当前为离线可验证引用合成模式（Grounded Extractive Synthesis），所有要点均严格绑定上方编号原文偏移，未引入未检索外部知识。"
    return header + "\n".join(selected_lines) + footer


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        response.close()
        raise RuntimeError("LLM endpoint redirected; configure final URL explicitly")


class GroundedChatEngine:
    """Produces grounded answers + citations, abstaining cleanly when evidence is insufficient."""

    def __init__(
        self,
        *,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
    ):
        self.llm_base_url = (llm_base_url or os.environ.get("LLM_BASE_URL") or "").strip() or None
        self.llm_api_key = (llm_api_key or os.environ.get("LLM_API_KEY") or "").strip() or None
        self.llm_model = (llm_model or os.environ.get("LLM_MODEL") or "gpt-4o-mini").strip()

    def _resolve_llm(self, override: dict[str, Any] | None) -> tuple[str | None, str | None, str]:
        if override:
            base = (override.get("base_url") or self.llm_base_url or "").strip() or None
            key = (override.get("api_key") or self.llm_api_key or "").strip() or None
            model = (override.get("model") or self.llm_model or "gpt-4o-mini").strip()
            return base, key, model
        return self.llm_base_url, self.llm_api_key, self.llm_model

    @staticmethod
    def _build_messages(query: str, hits: list[dict[str, Any]], citations: list[dict[str, Any]]) -> list[dict[str, str]]:
        ctx_blocks = []
        for idx, (hit, cit) in enumerate(zip(hits, citations), 1):
            body = hit.get("expanded_text") or hit.get("text", "")
            ctx_blocks.append(
                f"[{idx}] doc_id={cit['parent_id']} source={cit['source']} offset=[{cit['start']},{cit['end']})\n{body}"
            )
        context_str = "\n\n".join(ctx_blocks)
        system_prompt = (
            "你是严谨的企业知识库问答助手。请严格仅依据提供的带编号证据片段回答问题：\n"
            "1. 每个关键事实后必须标注对应角标，如 [1]、[2]。\n"
            "2. 严禁编造片段中不存在的数字、日期、条款或结论。\n"
            "3. 若证据不足以明确回答用户问题的核心条件，请直接拒绝回答并说明缺失的信息。"
        )
        user_prompt = f"问题：{query}\n\n已检索到的证据片段：\n{context_str}"
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _stream_openai(
        self, base_url: str, api_key: str, model: str, messages: list[dict[str, str]]
    ) -> Iterator[str]:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("LLM base_url must be HTTP(S)")
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps({"model": model, "messages": messages, "stream": True, "temperature": 0.1}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(req, timeout=45) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    choices = chunk.get("choices") or []
                    if choices:
                        delta = (choices[0].get("delta") or {}).get("content")
                        if delta:
                            yield delta
                except json.JSONDecodeError:
                    continue

    def stream_answer(
        self,
        search_result: dict[str, Any],
        query: str,
        *,
        llm_override: dict[str, Any] | None = None,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        hits = search_result.get("hits") or []
        citations = build_citations(hits)
        yield (
            "retrieval",
            {
                "hits": hits,
                "citations": citations,
                "evidence_sufficient": search_result.get("evidence_sufficient", False),
                "abstention_reason": search_result.get("abstention_reason"),
                "latency_ms": search_result.get("latency_ms", 0.0),
                "diagnostics": search_result.get("diagnostics"),
            },
        )

        if not search_result.get("evidence_sufficient") or not hits:
            reason = search_result.get("abstention_reason") or "未检索到满足置信度与权限条件的文档证据"
            abstain_msg = (
                "⚠️ **已触发无证据拒答门控（Abstention Gate）**\n\n"
                f"当前租户知识库中未检索到足够支撑该问题的权威证据（原因：`{reason}`）。\n"
                "为避免产生无依据幻觉，系统已拒绝生成回答。请核实权限范围、筛选条件或补充相关文档后重试。"
            )
            for i in range(0, len(abstain_msg), 18):
                yield ("delta", {"text": abstain_msg[i : i + 18]})
            yield ("citations", {"citations": [], "abstained": True, "mode": "abstention_gate"})
            yield ("done", {"abstained": True, "mode": "abstention_gate"})
            return

        base_url, api_key, model = self._resolve_llm(llm_override)
        mode = "extractive_offline"
        if base_url and api_key:
            mode = f"openai:{model}"
            messages = self._build_messages(query, hits, citations)
            try:
                for token in self._stream_openai(base_url, api_key, model, messages):
                    yield ("delta", {"text": token})
                yield ("citations", {"citations": citations, "abstained": False, "mode": mode})
                yield ("done", {"abstained": False, "mode": mode})
                return
            except Exception as exc:
                # Surface fallback note cleanly rather than silently breaking SSE stream
                fallback_note = f"> 外部 LLM 端点不可用（{exc}），已自动切换为离线抽取式溯源合成：\n\n"
                yield ("delta", {"text": fallback_note})
                mode = "extractive_fallback"

        text = _extractive_grounded_answer(query, hits, citations)
        for i in range(0, len(text), 24):
            yield ("delta", {"text": text[i : i + 24]})
        yield ("citations", {"citations": citations, "abstained": False, "mode": mode})
        yield ("done", {"abstained": False, "mode": mode})

    def answer(
        self,
        search_result: dict[str, Any],
        query: str,
        *,
        llm_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parts: list[str] = []
        retrieval_payload: dict[str, Any] = {}
        citations: list[dict[str, Any]] = []
        abstained = False
        mode = "extractive_offline"
        for event, data in self.stream_answer(search_result, query, llm_override=llm_override):
            if event == "retrieval":
                retrieval_payload = data
            elif event == "delta":
                parts.append(data["text"])
            elif event == "citations":
                citations = data["citations"]
                abstained = bool(data.get("abstained", False))
                mode = data.get("mode", mode)
        return {
            "answer": "".join(parts),
            "abstained": abstained,
            "mode": mode,
            "citations": citations,
            "hits": retrieval_payload.get("hits", []),
            "evidence_sufficient": retrieval_payload.get("evidence_sufficient", False),
            "abstention_reason": retrieval_payload.get("abstention_reason"),
            "latency_ms": retrieval_payload.get("latency_ms", 0.0),
            "diagnostics": retrieval_payload.get("diagnostics"),
        }
