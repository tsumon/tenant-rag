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
import threading
import urllib.parse
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
        if type(dim) is not int or dim <= 0:
            raise ValueError("dim must be a positive integer")
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _tokens(text):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            v[h % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, texts: list[str]) -> list[list[float]]:
        _validate_texts(texts)
        return [self._vec(t) for t in texts]


class EmbeddingResponseError(RuntimeError):
    """The provider returned an invalid embedding response, not bad user input."""


def _validate_texts(texts: list[str]) -> None:
    if not isinstance(texts, list) or any(not isinstance(t, str) for t in texts):
        raise ValueError("texts must be a list of strings")


class _RejectEmbeddingRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Never forward bearer credentials or document text to another URL.
        # Even same-origin redirects are rejected so the configured endpoint is
        # the only destination; users can set its final URL explicitly.
        response.close()
        raise EmbeddingResponseError("embedding endpoint redirected; configure its final URL explicitly")


class OpenAIEmbedder(Embedder):
    """OpenAI-compatible embeddings, with strict response validation.

    The first successful request pins the dimension for this instance. Reusing
    a database with a different model still requires re-indexing its documents.
    ``dim`` performs a provider request if no successful embedding exists yet.
    Empty batches return immediately without a network request. No automatic
    retries are made: retries can duplicate billable work.
    """

    _MAX_RESPONSE_BYTES = 32 * 1024 * 1024
    _FLOAT32_MAX = 3.4028234663852886e38

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "text-embedding-3-small",
        timeout: float = 30,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(base_url, str):
            raise ValueError("base_url must be an HTTP(S) URL")
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError("base_url must be an HTTP(S) URL without credentials, query, or fragment")
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or timeout <= 0):
            raise ValueError("timeout must be a positive finite number")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._dim: int | None = None
        self._dim_lock = threading.Lock()

    @property
    def dim(self) -> int:
        with self._dim_lock:
            dim = self._dim
        if dim is None:
            return len(self.embed(["dim probe"])[0])
        return dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        _validate_texts(texts)
        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise ValueError("provider input texts must be non-empty")
        req = urllib.request.Request(
            self.base_url + "/embeddings",
            data=json.dumps({"model": self.model, "input": texts,
                             "encoding_format": "float"}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(_RejectEmbeddingRedirects())
        with opener.open(req, timeout=self.timeout) as response:
            raw = response.read(self._MAX_RESPONSE_BYTES + 1)
        if len(raw) > self._MAX_RESPONSE_BYTES:
            raise EmbeddingResponseError("embedding response exceeds the size limit")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            # Do not echo provider payloads: they may contain private input.
            raise EmbeddingResponseError("embedding response is not valid UTF-8 JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise EmbeddingResponseError("embedding response must contain a data array")
        items = data["data"]
        if len(items) != len(texts):
            raise EmbeddingResponseError("embedding response count does not match input count")

        vectors: dict[int, list[float]] = {}
        batch_dim: int | None = None
        for item in items:
            if not isinstance(item, dict):
                raise EmbeddingResponseError("embedding response item must be an object")
            index = item.get("index")
            if type(index) is not int or not 0 <= index < len(texts) or index in vectors:
                raise EmbeddingResponseError("embedding response indexes must uniquely cover the input batch")
            values = item.get("embedding")
            if not isinstance(values, list) or not values:
                raise EmbeddingResponseError("embedding must be a non-empty numeric array")
            vector = []
            for value in values:
                if type(value) not in (int, float):
                    raise EmbeddingResponseError("embedding values must be finite float32 numbers")
                try:
                    value = float(value)
                except OverflowError as exc:
                    raise EmbeddingResponseError("embedding values must be finite float32 numbers") from exc
                if not math.isfinite(value) or abs(value) > self._FLOAT32_MAX:
                    raise EmbeddingResponseError("embedding values must be finite float32 numbers")
                vector.append(value)
            if batch_dim is not None and len(vector) != batch_dim:
                raise EmbeddingResponseError("embedding dimensions differ within the response")
            batch_dim = len(vector)
            vectors[index] = vector

        # Lock only the cache update, never the network call. Concurrent first
        # requests cannot silently pin conflicting dimensions.
        with self._dim_lock:
            if self._dim is not None and self._dim != batch_dim:
                raise EmbeddingResponseError("embedding dimension changed; use a consistent model and re-index")
            self._dim = batch_dim
        return [vectors[index] for index in range(len(texts))]
