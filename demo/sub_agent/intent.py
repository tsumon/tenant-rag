"""意图识别：关键词规则。够用、确定性、零成本。

生产升级路径：换成小模型分类器或 LLM function call，
接口不变（输入 question，输出 intent 字符串）。"""


def detect_intent(question: str) -> str:
    q = question.strip()
    if any(w in q for w in ("一共", "总共", "合计", "总计", "加起来", "花多少")):
        return "total"
    if any(w in q for w in ("最贵", "最高", "花钱最多", "最花钱")):
        return "max"
    if any(w in q for w in ("有哪些", "有哪", "列出", "所有订阅", "都订了", "哪些订阅")):
        # 带币种限定的列举（"用美元结算的订阅有哪些"）→ filter
        if detect_currency(q):
            return "filter"
        return "list"
    return "lookup"


def detect_currency(question: str) -> str | None:
    q = question.strip()
    if "美元" in q or "USD" in q.upper():
        return "USD"
    if "人民币" in q:
        return "CNY"
    if "欧元" in q or "EUR" in q.upper():
        return "EUR"
    return None
