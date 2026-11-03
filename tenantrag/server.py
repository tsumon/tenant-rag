"""M2: HTTP 服务。标准库 http.server，零依赖。

POST /v1/index        {"doc_id"?, "text", "metadata"?} -> {"doc_id": ...}
POST /v1/search       {"query", "k"?}                  -> {"hits": [...]}
DELETE /v1/docs/<id>                                  -> {"deleted": bool}
GET  /v1/docs?limit=&offset=                          -> {"docs": [...], "limit", "offset"}
GET  /v1/usage                                        -> {"tenant_id", "docs",
                                                           "quota_docs", "requests_today",
                                                           "rate_limit": {"capacity", "per_seconds"}}

鉴权：Authorization: Bearer <api_key>，无效或缺失 → 401。
限流：每租户令牌桶（默认 60/min），超限 → 429 + Retry-After。
错误映射：QuotaError/IsolationError → 403；JSON 非法/缺字段 → 400。
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .embed import FakeEmbedder, OpenAIEmbedder
from .service import AuthError, QuotaError, TenantRAG
from .store import IsolationError


class RateLimiter:
    """每 key 令牌桶。capacity 次突发，refill_per_sec 匀速回补。"""

    def __init__(self, capacity: int = 60, refill_per_sec: float = 1.0):
        self.capacity = float(capacity)
        self.refill = refill_per_sec
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """返回 (是否放行, 建议重试等待秒数)。"""
        now = time.monotonic()
        with self._lock:
            # 有界内存：桶太多时淘汰 1 小时没活动的
            if len(self._buckets) > 10000:
                self._buckets = {
                    k: v for k, v in self._buckets.items() if now - v[1] < 3600
                }
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                wait = (1.0 - tokens) / self.refill if self.refill > 0 else 60.0
                return False, wait
            self._buckets[key] = (tokens - 1.0, now)
            return True, 0.0


class Handler(BaseHTTPRequestHandler):
    server_version = "tenant-rag/0.3"
    # socket 超时（秒）：防 slowloris。客户端连上只发半截 header/body
    # 时，无超时的线程会永远挂起，攻击者低成本耗尽线程池（Round 5 实测）。
    # 正常请求（本地/内网，body 上限 10MB）10s 绰绰有余。
    timeout = 10

    # ---- 工具 ----
    def _send(self, code: int, obj: dict, headers: dict | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: int, error: str, **extra) -> None:
        self._send(code, {"error": error, **extra})

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if length <= 0 or length > 10 * 1024 * 1024:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _auth_key(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            key = auth[7:].strip()
            return key or None
        return None

    def _guard(self) -> str | None:
        """鉴权 + 限流 + 计量。通过返回 api_key，不通过时已回包、返回 None。"""
        key = self._auth_key()
        if key is None:
            self._err(401, "unauthorized", hint="Authorization: Bearer <api_key>")
            return None
        tid = self.server.rag.store.resolve_tenant(key)
        if tid is None:
            self._err(401, "unauthorized", hint="invalid api key")
            return None
        ok, wait = self.server.limiter.allow(tid)
        if not ok:
            # 429 也计量：让用户在 usage 里看到被限的请求
            self.server.rag.store.log_request(tid)
            self._send(
                429,
                {"error": "rate_limited", "retry_after": round(wait, 1)},
                headers={"Retry-After": str(int(wait) + 1)},
            )
            return None
        self.server.rag.store.log_request(tid)
        return key

    def _safe_err(self, code: int, error: str, **extra) -> None:
        """兜底回包：_send 本身也可能失败（客户端已断开），此时吞掉，
        避免异常穿透杀死工作线程、客户端收到连接重置而非 HTTP 响应。"""
        try:
            self._err(code, error, **extra)
        except Exception:
            pass

    # ---- 路由 ----
    def do_POST(self):
        try:
            path = urlparse(self.path).path
            if path == "/v1/index":
                self._handle_index()
            elif path == "/v1/search":
                self._handle_search()
            else:
                self._err(404, "not_found")
        except ValueError as e:
            self._safe_err(400, "bad_request", hint=str(e))
        except Exception:
            self._safe_err(500, "internal_error")

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            if path == "/v1/usage":
                self._handle_usage()
            elif path == "/v1/docs":
                self._handle_list()
            elif path == "/v1/health":
                self._send(200, {"ok": True})
            else:
                self._err(404, "not_found")
        except ValueError as e:
            self._safe_err(400, "bad_request", hint=str(e))
        except Exception:
            self._safe_err(500, "internal_error")

    def do_DELETE(self):
        try:
            parts = urlparse(self.path).path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "v1" and parts[1] == "docs" and parts[2]:
                self._handle_delete(unquote(parts[2]))
            else:
                self._err(404, "not_found")
        except ValueError as e:
            self._safe_err(400, "bad_request", hint=str(e))
        except Exception:
            self._safe_err(500, "internal_error")

    def log_message(self, fmt, *args):  # 安静：生产日志由网关打
        pass

    # ---- 处理器 ----
    def _handle_index(self):
        key = self._guard()
        if key is None:
            return
        body = self._read_json()
        if not isinstance(body, dict) or not body.get("text"):
            self._err(400, "bad_request", hint='{"text": "...", "doc_id"?, "metadata"?}')
            return
        try:
            doc_id = self.server.rag.index(
                key,
                body["text"],
                doc_id=body.get("doc_id"),
                metadata=body.get("metadata"),
            )
        except QuotaError as e:
            self._err(403, "quota_exceeded", hint=str(e))
            return
        except IsolationError as e:
            self._err(403, "forbidden", hint=str(e))
            return
        self._send(200, {"doc_id": doc_id})

    def _handle_search(self):
        key = self._guard()
        if key is None:
            return
        body = self._read_json()
        if not isinstance(body, dict) or not body.get("query"):
            self._err(400, "bad_request", hint='{"query": "...", "k"?}')
            return
        try:
            k = int(body.get("k", 10))
        except (TypeError, ValueError):
            self._err(400, "bad_request", hint="k must be int")
            return
        k = max(1, min(k, 100))
        try:
            hits = self.server.rag.search(key, body["query"], k=k)
        except AuthError:
            self._err(401, "unauthorized")
            return
        self._send(200, {"hits": hits})

    def _handle_delete(self, doc_id: str):
        key = self._guard()
        if key is None:
            return
        try:
            deleted = self.server.rag.delete(key, doc_id)
        except AuthError:
            self._err(401, "unauthorized")
            return
        self._send(200, {"deleted": deleted})

    def _handle_usage(self):
        key = self._guard()
        if key is None:
            return
        try:
            u = self.server.rag.usage(key)
        except AuthError:
            self._err(401, "unauthorized")
            return
        lim = self.server.limiter
        self._send(
            200,
            {
                **u,  # 已含 requests_today（service.usage 与 HTTP 对齐，Round 7）
                "rate_limit": {
                    "capacity": int(lim.capacity),
                    "per_seconds": int(lim.capacity / lim.refill)
                    if lim.refill > 0
                    else None,
                },
            },
        )

    def _handle_list(self):
        """GET /v1/docs?limit=&offset= —— 聚合类 Agent 需求走 HTTP 也能全量取。"""
        key = self._guard()
        if key is None:
            return
        qs = parse_qs(urlparse(self.path).query)
        try:
            limit = int(qs.get("limit", ["100"])[0])
            offset = int(qs.get("offset", ["0"])[0])
        except (TypeError, ValueError):
            self._err(400, "bad_request", hint="limit/offset must be int")
            return
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        try:
            docs = self.server.rag.list_docs(key, limit=limit, offset=offset)
        except AuthError:
            self._err(401, "unauthorized")
            return
        self._send(200, {"docs": docs, "limit": limit, "offset": offset})


def make_server(
    db_path: str = "tenantrag.db",
    host: str = "127.0.0.1",
    port: int = 8000,
    embedder=None,
    rate_capacity: int = 60,
    rate_per_seconds: int = 60,
) -> ThreadingHTTPServer:
    if type(rate_capacity) is not int or rate_capacity <= 0:
        raise ValueError("rate_capacity must be a positive integer")
    if type(rate_per_seconds) is not int or rate_per_seconds < 0:
        raise ValueError("rate_per_seconds must be a non-negative integer")
    rag = TenantRAG(db_path, embedder=embedder or FakeEmbedder())
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except Exception:
        rag.close()
        raise
    server.rag = rag
    # 注意：不再设 server.lock —— Store 自带 RLock，service.index 的
    # embedding 网络调用在锁外，慢请求不会堵死其他租户。
    server.limiter = RateLimiter(
        capacity=rate_capacity,
        refill_per_sec=rate_capacity / rate_per_seconds if rate_per_seconds else 0,
    )
    server.daemon_threads = True
    return server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--db", default=os.environ.get("TENANTRAG_DB", "tenantrag.db"))
    ap.add_argument("--rate", type=int, default=60, help="每租户每分钟请求数")
    ap.add_argument("--embed-base-url", default=os.environ.get("EMBED_BASE_URL"))
    ap.add_argument("--embed-model", default=os.environ.get("EMBED_MODEL", "text-embedding-3-small"))
    args = ap.parse_args()
    if args.rate <= 0:
        ap.error("--rate must be a positive integer")

    embedder = None
    if args.embed_base_url:
        api_key = os.environ.get("EMBED_API_KEY", "")
        try:
            embedder = OpenAIEmbedder(
                api_key=api_key, base_url=args.embed_base_url, model=args.embed_model
            )
        except ValueError as exc:
            ap.error(str(exc) + "; configure EMBED_API_KEY / --embed-base-url / --embed-model")
    server = make_server(args.db, args.host, args.port, embedder, args.rate, 60)
    print(f"tenant-rag serving on {args.host}:{args.port} db={args.db}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.rag.close()


if __name__ == "__main__":
    main()
