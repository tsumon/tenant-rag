"""订阅管家 Agent —— tenant-rag 的第一个真实应用（dogfood）。

架构：意图识别 → 数据获取 → 作答。
- lookup（"苹果21元是什么"）→ tenant-rag 混合检索
- 聚合（"一共多少钱 / 最贵 / 有哪些 / 美元结算的有哪些"）→ list_docs 全量聚合
  （RAG 应用的真实分工：聚合不依赖检索召回，问答才走检索）
- 作答默认模板，零模型成本；composer 可换成 LLM（OpenAI 兼容接口）
- 每个回答带 sources: [doc_id]，可追溯 —— Agent 回答必须能查到出处
"""
from __future__ import annotations

from .intent import detect_currency, detect_intent

# 传给 composer 的单条文本上限：模板作答无所谓，但换成 LLM composer 时
# 长文本会爆 token。sources 始终是完整 doc_id，可追溯不受影响。
MAX_TEXT_CHARS = 500


def _trunc(text: str) -> str:
    return text if len(text) <= MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS] + "……"


class SubscriptionAgent:
    """一个用户 = 一个 api_key = 一个租户。Agent 永远只看得到自己的账单。"""

    def __init__(self, rag, api_key: str, composer=None):
        self.rag = rag
        self.api_key = api_key
        self.composer = composer or self._template_compose

    # ---- 主入口 ----
    def ask(self, question: str) -> dict:
        intent = detect_intent(question)
        if intent in ("total", "max", "list", "filter"):
            docs = self._all_docs()
            ccy = detect_currency(question)
            if ccy and intent in ("total", "max", "filter"):
                # 显式币种限定（"用美元结算的一共多少钱"）必须生效：
                # 之前只对 filter 意图过滤，total/max 会静默忽略限定
                # （Round 7 实测：问 USD 的 total 把 CNY 也算进去了）。
                docs = [d for d in docs if d["metadata"].get("currency") == ccy]
                if not docs:
                    return {
                        "answer": f"没有找到 {ccy} 结算的订阅。",
                        "sources": [],
                        "intent": intent,
                    }
            return self._answer_aggregate(intent, question, docs)
        docs = self.rag.search(self.api_key, question, k=3)
        return self._answer_lookup(question, docs)

    def _all_docs(self) -> list[dict]:
        """分页取全量：list_docs 有 limit 上限，直接取第一页会在大租户下
        静默漏算（汇总/最贵都会错）。"""
        docs, offset, page = [], 0, 500
        while True:
            batch = self.rag.list_docs(self.api_key, limit=page, offset=offset)
            docs.extend(batch)
            if len(batch) < page:
                break
            offset += page
        return docs

    # ---- 聚合类 ----
    def _answer_aggregate(self, intent: str, question: str, docs: list[dict]) -> dict:
        if not docs:
            return {"answer": "你还没有记录任何订阅。", "sources": [], "intent": intent}
        priced = [d for d in docs if d["metadata"].get("price_monthly") is not None]

        if intent == "total":
            if not priced:
                answer = f"你有 {len(docs)} 个订阅，均无价格信息。"
            else:
                by_ccy: dict[str, float] = {}
                for d in priced:
                    c = d["metadata"].get("currency", "CNY")
                    by_ccy[c] = by_ccy.get(c, 0) + d["metadata"]["price_monthly"]
                parts = [f"{v:.2f} {c}" for c, v in sorted(by_ccy.items())]
                answer = f"你有 {len(docs)} 个订阅，每月合计约 {' + '.join(parts)}。"
        elif intent == "max":
            if not priced:
                answer = "你的订阅都没有价格信息，无法比较。"
            else:
                # 不跨币种比大小：各币种取各自最贵（汇率是另一个话题）
                top_by_ccy: dict[str, dict] = {}
                for d in priced:
                    c = d["metadata"].get("currency", "CNY")
                    if (c not in top_by_ccy
                            or d["metadata"]["price_monthly"]
                            > top_by_ccy[c]["metadata"]["price_monthly"]):
                        top_by_ccy[c] = d
                bits = [
                    f"「{d['metadata']['name']}」"
                    f"（{d['metadata']['price_monthly']} {c}/月）"
                    for c, d in sorted(top_by_ccy.items())
                ]
                answer = "最贵的是" + "；".join(bits) + "。"
        else:  # list / filter
            lines = [
                f"{i}. {d['metadata'].get('name', d['doc_id'])}"
                f"（{d['metadata'].get('price_monthly', '?')} "
                f"{d['metadata'].get('currency', '')}/月）"
                for i, d in enumerate(docs, 1)
            ]
            answer = "你的订阅：\n" + "\n".join(lines)
        return {
            "answer": answer,
            "sources": [d["doc_id"] for d in docs],
            "intent": intent,
        }

    # ---- 检索类 ----
    def _answer_lookup(self, question: str, hits: list[dict]) -> dict:
        if not hits:
            return {"answer": "没找到相关的订阅记录。", "sources": [], "intent": "lookup"}
        # 传给 composer 前截断长文本：换成 LLM composer 时不截断会爆 token。
        slim = [{**h, "text": _trunc(h["text"])} for h in hits]
        return {
            "answer": self.composer(question, slim),
            "sources": [h["doc_id"] for h in hits],
            "intent": "lookup",
        }

    @staticmethod
    def _template_compose(question: str, hits: list[dict]) -> str:
        top = hits[0]
        name = top["metadata"].get("name", top["doc_id"])
        others = [h["metadata"].get("name", h["doc_id"]) for h in hits[1:]]
        tail = f"（还可能相关：{'、'.join(others)}）" if others else ""
        return f"「{name}」：{top['text']}{tail}"
