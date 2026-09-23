"""Dogfood 种子数据：两个租户，各自的订阅账单。

刻意安排：两人都订了"腾讯视频"但价格不同 ——
同一问题在不同租户嘴里必须得到不同答案，这就是隔离的价值。
"""
from __future__ import annotations


def _bill(name, text, price, currency="CNY", cycle="monthly"):
    monthly = price / 12 if cycle == "yearly" else price
    return {
        "text": text,
        "metadata": {
            "name": name,
            "price": price,
            "currency": currency,
            "cycle": cycle,
            "price_monthly": round(monthly, 2),
        },
    }


ALICE_BILLS = {
    "bill-icloud": _bill("iCloud+", "iCloud+ 2TB 每月 21 元自动续费", 21),
    "bill-txvideo": _bill("腾讯视频", "腾讯视频 VIP 年费 198 元", 198, cycle="yearly"),
    "bill-spotify": _bill("Spotify", "Spotify Premium 每月 18 元", 18),
    "bill-dida": _bill("滴答清单", "滴答清单高级会员年费 99 元", 99, cycle="yearly"),
    "bill-chatgpt": _bill("ChatGPT Plus", "ChatGPT Plus 每月 20 美元", 20, currency="USD"),
}

BOB_BILLS = {
    "bill-txvideo": _bill("腾讯视频", "腾讯视频连续包月每月 30 元", 30),
    "bill-netease": _bill("网易云音乐", "网易云音乐黑胶 VIP 每月 15 元", 15),
    "bill-bilibili": _bill("哔哩哔哩", "哔哩哔哩大会员年费 148 元", 148, cycle="yearly"),
    "bill-notion": _bill("Notion Plus", "Notion Plus 每月 10 美元", 10, currency="USD"),
}


def seed_demo(rag) -> dict[str, str]:
    """注册两个租户并写入账单。返回 {名字: api_key}。"""
    keys = {"小李": "sk-alice-demo", "小王": "sk-bob-demo"}
    rag.register("tenant-alice", keys["小李"])
    rag.register("tenant-bob", keys["小王"])
    for doc_id, b in ALICE_BILLS.items():
        rag.index(keys["小李"], b["text"], doc_id=doc_id, metadata=b["metadata"])
    for doc_id, b in BOB_BILLS.items():
        rag.index(keys["小王"], b["text"], doc_id=doc_id, metadata=b["metadata"])
    return keys
