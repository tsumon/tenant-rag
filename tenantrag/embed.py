"""Embedding 接口。

FakeEmbedder：确定性哈希词袋向量，离线可用，测试/评测零成本。
  注意：它是词法级向量，语义能力弱——评测数字只验证管线正确性，
  不代表线上效果。线上用 OpenAIEmbedder（或任何 OpenAI 兼容接口）。

设计取舍：embedding 是纯函数接口，换模型不用改存储和检索代码；
向量以 float32 blob 存 SQLite，个人级语料暴力检索足够（升级路径见 README）。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.request


class Embedder:
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _tokens(text: str) -> list[str]:
    """简单分词：CJK 逐字，其余按字母数字成词。只给 FakeEmbedder 用。"""
    out: list[str] = []
    for part in text.lower().split():
        buf = ""
        for ch in part:
            if _CJK_RE.match(ch):
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(ch)
            elif ch.isalnum():
                buf += ch
            else:
                if buf:
                    out.append(buf)
                    buf = ""
        if buf:
            out.append(buf)
    return out


class FakeEmbedder(Embedder):
    def __init__(self, dim: int = 256):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _tokens(text):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            v[h % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class OpenAIEmbedder(Embedder):
    """OpenAI 兼容的 /v1/embeddings：可用 OpenAI / SiliconFlow / 本地 vLLM 等。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "text-embedding-3-small",
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed(["dim probe"])[0])
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        req = urllib.request.Request(
            self.base_url + "/embeddings",
            data=json.dumps({"model": self.model, "input": texts}).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode())
        items = sorted(data["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in items]
