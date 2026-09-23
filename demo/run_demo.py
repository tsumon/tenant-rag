"""订阅管家 Agent 演示：双租户问答 transcript。

离线运行，零模型成本：
    python3 demo/run_demo.py

看点：
1. 同一问题（"腾讯视频多少钱"）在不同租户嘴里答案不同 —— 隔离
2. lookup 走混合检索，聚合走全量数据 —— RAG 应用的真实分工
3. 每个回答带 sources，可追溯
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from tenantrag import TenantRAG
from tenantrag.embed import FakeEmbedder

from seed import seed_demo
from sub_agent import SubscriptionAgent


def main():
    db = os.path.join(tempfile.mkdtemp(), "demo.db")
    rag = TenantRAG(db, embedder=FakeEmbedder())
    keys = seed_demo(rag)

    agents = {name: SubscriptionAgent(rag, key) for name, key in keys.items()}

    script = [
        ("小李", "苹果21元是什么"),
        ("小李", "我每个月一共花多少钱"),
        ("小李", "最贵的订阅是哪个"),
        ("小李", "腾讯视频多少钱"),
        ("小王", "腾讯视频多少钱"),
        ("小王", "我有哪些订阅"),
        ("小王", "用美元结算的订阅有哪些"),
    ]

    for who, q in script:
        r = agents[who].ask(q)
        print(f"【{who}】问：{q}")
        print(f"  答：{r['answer']}")
        print(f"  （intent={r['intent']}，sources={r['sources']}）\n")

    rag.close()


if __name__ == "__main__":
    main()
