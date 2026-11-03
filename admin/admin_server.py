"""租户管理后台：纯标准库 Web UI，反向代理到 M2 HTTP API。

只消费 M2 的公开 HTTP 接口，不直连 DB、不改核心代码。

运行：
  1. 先起 M2 API：
     PYTHONPATH=. python3 -m tenantrag.server --db tenantrag.db --port 8000
  2. 再起后台：
     python3 admin/admin_server.py --api http://127.0.0.1:8000 --port 8002
  3. 浏览器打开 http://127.0.0.1:8002/ ，输入租户 API key 登录。

设计说明：
- key 只存在浏览器内存（JS 变量），不落盘；退出即清。
- 浏览器同源请求 /v1/*，本服务原样代理到 M2（含 Authorization 头），
  因此没有 CORS 问题，也不需要给 M2 加跨域头。
- 上传的文本由 M2 服务端 embedder 自动向量化（默认 FakeEmbedder 离线；
  M2 用 --embed-base-url 接真实模型后，后台无需任何改动）。
- 本地工具：只监听 127.0.0.1，不要暴露到公网。
"""

from __future__ import annotations

import argparse
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 只允许代理这些 M2 路由，其他一律 404，避免把管理后台当成开放代理。
PROXY_PREFIXES = ("/v1/index", "/v1/search", "/v1/docs", "/v1/usage", "/v1/health")


class AdminHandler(BaseHTTPRequestHandler):
    timeout = 10
    server_version = "TenantRAG-Admin/1.0"

    def log_message(self, fmt, *args):  # 安静
        pass

    # ---- 页面 ----
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/admin"):
            self._serve_index()
        elif self.path.startswith("/v1/"):
            self._proxy()
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._err(404, "not_found")

    def do_POST(self):
        if self.path.startswith("/v1/"):
            self._proxy()
        else:
            self._err(404, "not_found")

    def do_DELETE(self):
        if self.path.startswith("/v1/"):
            self._proxy()
        else:
            self._err(404, "not_found")

    def _serve_index(self):
        try:
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                data = f.read()
        except OSError:
            self._err(500, "index.html 缺失")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- 反向代理 ----
    def _proxy(self):
        path = self.path.split("?", 1)[0]
        if path not in PROXY_PREFIXES and not (path.startswith("/v1/docs/") and path.count("/") == 3):
            self._err(404, "not_found")
            return
        api = self.server.api_base.rstrip("/")
        url = api + self.path  # 保留原始 query string

        body = None
        if self.command in ("POST", "PUT", "PATCH"):
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                length = 0
            if length <= 0 or length > 10 * 1024 * 1024:
                self._err(400, "bad_request", hint="Content-Length must be between 1 and 10485760")
                return
            body = self.rfile.read(length)

        req = urllib.request.Request(url, data=body, method=self.command)
        for h in ("Authorization", "Content-Type"):
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                self.send_response(r.status)
                self.send_header(
                    "Content-Type",
                    r.headers.get("Content-Type", "application/json"),
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if e.headers.get("Retry-After"):
                self.send_header("Retry-After", e.headers["Retry-After"])
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self._err(502, "bad_gateway", hint="M2 API 不可达")

    def _err(self, code, error, hint=""):
        import json

        payload = {"error": error}
        if hint:
            payload["hint"] = hint
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser(description="tenant-rag 租户管理后台")
    ap.add_argument("--api", default="http://127.0.0.1:8000",
                    help="M2 API 地址（默认 http://127.0.0.1:8000）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8002)
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AdminHandler)
    server.daemon_threads = True
    server.api_base = args.api
    print(f"管理后台 http://{args.host}:{args.port}/  → 代理 {args.api}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
