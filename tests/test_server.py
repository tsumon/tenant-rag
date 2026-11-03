"""M2 HTTP 服务测试：鉴权、隔离、配额、限流、计量。"""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

from tenantrag import TenantRAG
from tenantrag.embed import FakeEmbedder
from tenantrag.server import make_server


def _req(server, method, path, key=None, body=None):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        with e:
            return e.code, json.loads(e.read().decode()), dict(e.headers)


class ServerTest(unittest.TestCase):
    def _start(self, rate_capacity=60, rate_per_seconds=60):
        self.tmp = tempfile.TemporaryDirectory()
        db = os.path.join(self.tmp.name, "s.db")
        rag = TenantRAG(db, embedder=FakeEmbedder())
        rag.register("tenant-a", "test-key-a")
        rag.register("tenant-b", "test-key-b")
        rag.register("tenant-q", "test-key-q", quota_docs=1)
        rag.close()
        self.server = make_server(db, "127.0.0.1", 0,
                                  embedder=FakeEmbedder(),
                                  rate_capacity=rate_capacity,
                                  rate_per_seconds=rate_per_seconds)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        return self.server

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.rag.close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def test_health_no_auth(self):
        self._start()
        code, body, _ = _req(self.server, "GET", "/v1/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_unauthorized(self):
        self._start()
        code, body, _ = _req(self.server, "GET", "/v1/usage")
        self.assertEqual(code, 401)
        code, body, _ = _req(self.server, "GET", "/v1/usage", key="nope")
        self.assertEqual(code, 401)

    def test_index_search_roundtrip(self):
        self._start()
        code, body, _ = _req(self.server, "POST", "/v1/index", "test-key-a",
                             {"doc_id": "d1", "text": "腾讯视频 VIP 年费 198 元"})
        self.assertEqual(code, 200)
        self.assertEqual(body["doc_id"], "d1")
        code, body, _ = _req(self.server, "POST", "/v1/search", "test-key-a",
                             {"query": "腾讯视频多少钱"})
        self.assertEqual(code, 200)
        self.assertEqual(body["hits"][0]["doc_id"], "d1")

    def test_isolation_over_http(self):
        self._start()
        _req(self.server, "POST", "/v1/index", "test-key-a",
             {"doc_id": "da", "text": "A 的机密账单"})
        _req(self.server, "POST", "/v1/index", "test-key-b",
             {"doc_id": "db", "text": "B 的机密账单"})
        code, body, _ = _req(self.server, "POST", "/v1/search", "test-key-a",
                             {"query": "机密账单"})
        ids = [h["doc_id"] for h in body["hits"]]
        self.assertIn("da", ids)
        self.assertNotIn("db", ids)
        # 跨租户删除：返回 false，不报错不删
        code, body, _ = _req(self.server, "DELETE", "/v1/docs/db", "test-key-a")
        self.assertEqual(code, 200)
        self.assertFalse(body["deleted"])
        code, body, _ = _req(self.server, "POST", "/v1/search", "test-key-b",
                             {"query": "机密账单"})
        self.assertIn("db", [h["doc_id"] for h in body["hits"]])

    def test_quota_403(self):
        self._start()
        code, _, _ = _req(self.server, "POST", "/v1/index", "test-key-q",
                          {"doc_id": "q1", "text": "one"})
        self.assertEqual(code, 200)
        code, body, _ = _req(self.server, "POST", "/v1/index", "test-key-q",
                             {"doc_id": "q2", "text": "two"})
        self.assertEqual(code, 403)
        self.assertEqual(body["error"], "quota_exceeded")

    def test_bad_request_400(self):
        self._start()
        code, body, _ = _req(self.server, "POST", "/v1/index", "test-key-a",
                             {"no_text": 1})
        self.assertEqual(code, 400)

    def test_rate_limit_429(self):
        self._start(rate_capacity=2, rate_per_seconds=0)  # 0 回补，确定性
        for _ in range(2):
            code, _, _ = _req(self.server, "GET", "/v1/usage", "test-key-a")
            self.assertEqual(code, 200)
        code, body, headers = _req(self.server, "GET", "/v1/usage", "test-key-a")
        self.assertEqual(code, 429)
        self.assertEqual(body["error"], "rate_limited")
        self.assertIn("Retry-After", headers)

    def test_usage_metering(self):
        self._start()
        _req(self.server, "POST", "/v1/index", "test-key-a",
             {"doc_id": "d1", "text": "hello"})
        _req(self.server, "POST", "/v1/search", "test-key-a", {"query": "hello"})
        code, body, _ = _req(self.server, "GET", "/v1/usage", "test-key-a")
        self.assertEqual(code, 200)
        self.assertEqual(body["tenant_id"], "tenant-a")
        self.assertEqual(body["docs"], 1)
        self.assertEqual(body["requests_today"], 3)  # index+search+usage
        self.assertEqual(body["rate_limit"]["capacity"], 60)


if __name__ == "__main__":
    unittest.main()
