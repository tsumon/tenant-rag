"""Bug 回归测试：找 bug 专项中发现并修复的问题。"""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

from tenantrag import TenantRAG
from tenantrag.embed import Embedder, FakeEmbedder
from tenantrag.server import make_server


class RobustnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rag = TenantRAG(os.path.join(self.tmp.name, "t.db"),
                             embedder=FakeEmbedder())
        self.rag.register("t", "test-api-key")

    def tearDown(self):
        self.rag.close()
        self.tmp.cleanup()

    def test_cosine_not_dot_product(self):
        # 未归一化向量必须按余弦排序：[1,0] 与 [1,0] 余弦=1 > [2,2] 的 0.707
        self.rag.store.upsert_doc("t", "A", "a", {}, [2.0, 2.0])
        self.rag.store.upsert_doc("t", "B", "b", {}, [1.0, 0.0])
        res = self.rag.store.vector_search("t", [1.0, 0.0], 10)
        self.assertEqual([d for d, _ in res], ["B", "A"])
        self.assertAlmostEqual(res[0][1], 1.0, places=5)

    def test_index_rejects_bad_text(self):
        with self.assertRaises(ValueError):
            self.rag.index("test-api-key", 123)
        with self.assertRaises(ValueError):
            self.rag.index("test-api-key", "")
        with self.assertRaises(ValueError):
            self.rag.index("test-api-key", "   ")

    def test_index_rejects_bad_metadata(self):
        with self.assertRaises(ValueError):
            self.rag.index("test-api-key", "hi", metadata="not-a-dict")
        with self.assertRaises(ValueError):
            self.rag.index("test-api-key", "hi", doc_id=123)

    def test_search_rejects_bad_query(self):
        with self.assertRaises(ValueError):
            self.rag.search("test-api-key", 123)

    def test_search_negative_k_clamped(self):
        self.rag.index("test-api-key", "hello world", doc_id="d1")
        hits = self.rag.search("test-api-key", "hello", k=-1)
        self.assertEqual([h["doc_id"] for h in hits], ["d1"])

    def test_duplicate_tenant_rejected(self):
        with self.assertRaises(ValueError):
            self.rag.register("t", "another-key")

    def test_concurrent_index_quota_race(self):
        # 8 线程并发写，配额 5：最终恰好 5 篇，不超卖、不崩
        rag = TenantRAG(os.path.join(self.tmp.name, "c.db"),
                        embedder=FakeEmbedder())
        rag.register("t", "test-api-key", quota_docs=5)
        errors = []

        def worker(i):
            try:
                rag.index("test-api-key", f"doc {i}", doc_id=f"d{i}")
            except Exception as e:  # QuotaError 预期内
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(rag.store.doc_count("t"), 5)
        rag.close()


class BrokenEmbedder(Embedder):
    dim = 256

    def embed(self, texts):
        raise RuntimeError("embedding backend down")


class ServerRobustnessTest(unittest.TestCase):
    def _start(self, embedder):
        self.tmp = tempfile.TemporaryDirectory()
        db = os.path.join(self.tmp.name, "s.db")
        rag = TenantRAG(db, embedder=FakeEmbedder())
        rag.register("t", "test-api-key")
        rag.close()
        self.server = make_server(db, "127.0.0.1", 0, embedder=embedder)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def _call(self, method, path, key=None, body=None):
        url = f"http://127.0.0.1:{self.server.server_address[1]}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_bad_text_type_is_400_not_reset(self):
        self._start(FakeEmbedder())
        code, body = self._call("POST", "/v1/index", "test-api-key", {"text": 123})
        self.assertEqual(code, 400)
        code, body = self._call("POST", "/v1/search", "test-api-key", {"query": 123})
        self.assertEqual(code, 400)

    def test_embed_failure_is_500_json(self):
        self._start(BrokenEmbedder())
        code, body = self._call("POST", "/v1/index", "test-api-key", {"text": "hi"})
        self.assertEqual(code, 500)
        self.assertEqual(body["error"], "internal_error")


class Round2Test(unittest.TestCase):
    """第二轮找 bug（2026-09-23）新增回归。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rag = TenantRAG(
            os.path.join(self.tmp.name, "t.db"), embedder=FakeEmbedder()
        )
        self.rag.register("t", "test-api-key")

    def tearDown(self):
        self.rag.close()
        self.tmp.cleanup()

    def test_dup_api_key_is_valueerror(self):
        with self.assertRaises(ValueError):
            self.rag.register("t2", "test-api-key")

    def test_register_bad_inputs(self):
        for tid, key, quota in [
            ("", "k2", 10),
            (None, "k2", 10),
            ("t3", "", 10),
            ("t3", None, 10),
            ("t3", "k3", -1),
        ]:
            with self.assertRaises(ValueError):
                self.rag.register(tid, key, quota)

    def test_none_key_is_auth_error(self):
        from tenantrag.service import AuthError

        for fn in (
            lambda: self.rag.search(None, "hi"),
            lambda: self.rag.index(None, "hi"),
            lambda: self.rag.list_docs(None),
        ):
            with self.assertRaises(AuthError):
                fn()

    def test_fts_empty_query_returns_empty(self):
        self.assertEqual(self.rag.store.fts_search("t", "", 10), [])

    def test_float_k_coerced(self):
        self.rag.index("test-api-key", "hello world")
        hits = self.rag.search("test-api-key", "hello", k=2.5)
        self.assertLessEqual(len(hits), 2)

    def test_list_docs_pagination(self):
        for i in range(5):
            self.rag.index("test-api-key", f"doc {i}", doc_id=f"d{i}")
        page1 = self.rag.list_docs("test-api-key", limit=2, offset=0)
        page2 = self.rag.list_docs("test-api-key", limit=2, offset=2)
        page3 = self.rag.list_docs("test-api-key", limit=2, offset=4)
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        self.assertEqual(len(page3), 1)
        ids = [d["doc_id"] for d in page1 + page2 + page3]
        self.assertEqual(len(set(ids)), 5)


class ListRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, method, path, key=None):
        url = f"http://127.0.0.1:{self.server.server_address[1]}{path}"
        req = urllib.request.Request(url, method=method)
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_list_docs_route_paginates(self):
        from tenantrag.server import make_server

        db = os.path.join(self.tmp.name, "t.db")
        self.server = make_server(db, port=0, embedder=FakeEmbedder())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        try:
            self.server.rag.register("t", "test-api-key")
            for i in range(3):
                self.server.rag.index("test-api-key", f"doc {i}", doc_id=f"d{i}")
            code, body = self._call("GET", "/v1/docs?limit=2&offset=1", "test-api-key")
            self.assertEqual(code, 200)
            self.assertEqual(len(body["docs"]), 2)
            self.assertEqual(body["offset"], 1)
            code, _ = self._call("GET", "/v1/docs", None)
            self.assertEqual(code, 401)
            code, _ = self._call("GET", "/v1/docs?limit=x", "test-api-key")
            self.assertEqual(code, 400)
        finally:
            self.server.shutdown()
            self.server.rag.close()


class ConcurrencyTest(unittest.TestCase):
    """Round 3：单连接多线程并发。check_same_thread=False 只关掉线程
    亲和检查，并不让并发 execute() 变安全——读操作不加锁会报
    InterfaceError: bad parameter or other API misuse（实测 16 线程
    混合读写 14 个错），连带 resolve_tenant 偶发返回 None（合法 key
    误判 401）。修法：Store 所有 _con 访问全部走 RLock。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rag = TenantRAG(
            os.path.join(self.tmp.name, "t.db"), embedder=FakeEmbedder()
        )
        self.rag.register("t", "test-api-key", quota_docs=100000)

    def tearDown(self):
        self.rag.close()
        self.tmp.cleanup()

    def test_mixed_rw_no_interface_error(self):
        for i in range(10):
            self.rag.index("test-api-key", f"种子 {i}", doc_id=f"s{i}")
        errors = []

        def writer(n):
            try:
                for i in range(25):
                    self.rag.index("test-api-key", f"写 {n}-{i}", doc_id=f"w{n}-{i}")
                    if i % 5 == 0:
                        self.rag.delete("test-api-key", f"w{n}-{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"writer{n}: {type(e).__name__}: {e}")

        def reader(n):
            try:
                for i in range(25):
                    self.rag.search("test-api-key", f"查 {i}", k=5)
                    self.rag.list_docs("test-api-key", limit=5)
            except Exception as e:  # noqa: BLE001
                errors.append(f"reader{n}: {type(e).__name__}: {e}")

        ts = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        ts += [threading.Thread(target=reader, args=(n,)) for n in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        self.assertFalse(any(t.is_alive() for t in ts), "线程卡死")
        self.assertEqual(errors, [], f"并发异常: {errors[:3]}")
        # docs 与 fts 行数一致：无半提交
        c1 = self.rag.store._con.execute(
            "SELECT COUNT(*) FROM docs WHERE tenant_id='t'"
        ).fetchone()[0]
        c2 = self.rag.store._con.execute(
            "SELECT COUNT(*) FROM docs_fts WHERE tenant_id='t'"
        ).fetchone()[0]
        self.assertEqual(c1, c2)

    def test_cross_instance_quota_no_oversell(self):
        """两实例同 DB：RLock 跨实例不协调，配额检查→写入必须靠
        BEGIN IMMEDIATE 跨连接原子，否则 quota=30 会写出 31（实测）。"""
        db = os.path.join(self.tmp.name, "two.db")
        r1 = TenantRAG(db, embedder=FakeEmbedder())
        r1.register("t", "test-api-key", quota_docs=30)
        r2 = TenantRAG(db, embedder=FakeEmbedder())
        from tenantrag.service import QuotaError

        try:

            def w(rag, n):
                for i in range(25):
                    try:
                        rag.index("test-api-key", f"内容 {n}-{i}", doc_id=f"x{n}-{i}")
                    except QuotaError:
                        pass

            ts = [
                threading.Thread(target=w, args=(r, n))
                for r in (r1, r2)
                for n in range(4)
            ]
            for t in ts:
                t.start()
            for t in ts:
                t.join(60)
            self.assertLessEqual(r1.store.doc_count("t"), 30)
        finally:
            r1.close()
            r2.close()


class FtsCaseTest(unittest.TestCase):
    """Round 4：FTS5 trigram 默认 ASCII 大小写不敏感（实测），
    FakeEmbedder 也 lower，两侧一致——"Tencent Video" 用 "tencent"
    查，fts 通道必须命中。锁死该行为，防止将来换 tokenizer
    （如 case_sensitive 1）时英文 FTS 静默失效。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rag = TenantRAG(
            os.path.join(self.tmp.name, "t.db"), embedder=FakeEmbedder()
        )
        self.rag.register("t", "test-api-key")
        self.rag.index("test-api-key", "Tencent Video 会员 198 元每年", doc_id="d1")
        self.rag.index("test-api-key", "iCloud+ 订阅 每月 21 元", doc_id="d2")

    def tearDown(self):
        self.rag.close()
        self.tmp.cleanup()

    def _fts_matched(self, query):
        hits = self.rag.search("test-api-key", query, k=3)
        return {h["doc_id"]: h["matched"] for h in hits}

    def test_case_insensitive_fts(self):
        for q in ("tencent", "TENCENT", "Tencent"):
            m = self._fts_matched(q)
            self.assertIn("fts", m.get("d1", []), f"query={q}")
        for q in ("icloud", "ICLOUD", "iCloud"):
            m = self._fts_matched(q)
            self.assertIn("fts", m.get("d2", []), f"query={q}")

    def test_digit_token(self):
        m = self._fts_matched("198")
        self.assertIn("fts", m.get("d1", []))


class SecurityTest(unittest.TestCase):
    """Round 5：slowloris 超时、深层嵌套 JSON、弱 key。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _srv(self, timeout=2):
        from tenantrag.server import Handler, make_server

        old = Handler.timeout
        Handler.timeout = timeout
        srv = make_server(os.path.join(self.tmp.name, "t.db"), port=0,
                          embedder=FakeEmbedder())
        srv.rag.register("t", "test-api-key")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, old

    def _stop(self, srv, old):
        from tenantrag.server import Handler

        srv.shutdown()
        srv.rag.close()
        Handler.timeout = old

    def test_slowloris_connection_times_out(self):
        """半截 header 的连接必须在 timeout 内被服务端断开，
        否则攻击者低成本耗尽线程池（Round 5 实测无超时时 11s 仍占线程）。"""
        import socket

        srv, old = self._srv(timeout=2)
        try:
            port = srv.server_address[1]
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            s.sendall(b"POST /v1/search HTTP/1.1\r\nHost: x\r\n"
                      b"Authorization: Bearer test-api-key\r\n"
                      b"Content-Length: 100\r\n")
            # 半截请求：只发 header，不发 body，干等
            s.settimeout(8)
            try:
                data = s.recv(100)
            except socket.timeout:
                self.fail("服务端 8s 内未断开 slowloris 连接")
            # 服务端超时后会关闭连接：recv 返回 b""（或 RST 抛错）
            self.assertEqual(data, b"")
            s.close()
        finally:
            self._stop(srv, old)

    def test_deep_nested_json_500_and_survives(self):
        """深层嵌套 JSON 触发 RecursionError：进程不崩，返回 500 JSON，
        后续请求正常（Round 5 实测确认）。"""
        srv, old = self._srv()
        try:
            port = srv.server_address[1]
            deep = ("[" * 100000 + "]" * 100000).encode()

            def post(raw):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/search", data=raw, method="POST")
                req.add_header("Authorization", "Bearer test-api-key")
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        return r.status, r.read()[:40]
                except urllib.error.HTTPError as e:
                    return e.code, e.read()[:40]

            code, body = post(deep)
            self.assertEqual(code, 500)
            self.assertIn(b"internal_error", body)
            # 服务还活着
            code, _ = post(b'{"query": "hi"}')
            self.assertEqual(code, 200)
        finally:
            self._stop(srv, old)

    def test_weak_api_key_rejected(self):
        """api_key 是唯一的鉴权因子：<8 字符直接拒（产品决策，
        见 store.create_tenant 注释）；8 字符可注册。"""
        rag = TenantRAG(os.path.join(self.tmp.name, "w.db"),
                        embedder=FakeEmbedder())
        try:
            for weak in ("1", "k", "test", "1234567"):
                with self.assertRaises(ValueError, msg=weak):
                    rag.register("t-" + weak, weak)
            rag.register("t-ok", "12345678")  # 恰 8 位，通过
        finally:
            rag.close()


class ConsistencyTest(unittest.TestCase):
    """Round 6：多条 SQL 的原子性、迁移失败回滚、usage retention。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        self.rag = TenantRAG(self.db, embedder=FakeEmbedder())
        self.rag.register("t", "test-api-key")

    def tearDown(self):
        self.rag.close()
        self.tmp.cleanup()

    def test_upsert_rollback_on_fts_failure(self):
        """docs 写成功但 FTS 失败 → 整体回滚，不留半提交、不占写锁。"""
        s = self.rag.store
        s._con.execute("DROP TABLE docs_fts")
        s._con.commit()
        with self.assertRaises(Exception):
            s.upsert_doc("t", "d1", "hello", embedding=[0.1] * 256)
        self.assertEqual(s.doc_count("t"), 0)
        self.assertFalse(s._con.in_transaction)

    def test_delete_miss_releases_txn(self):
        """delete miss（0 行）也要收尾事务，否则开事务占着
        RESERVED 写锁，挡住其他连接的写。"""
        s = self.rag.store
        self.assertFalse(s.delete_doc("t", "nope"))
        self.assertFalse(s._con.in_transaction)

    def test_purge_usage(self):
        s = self.rag.store
        s._con.execute("INSERT INTO usage_meter VALUES('t','2000-01-01',5)")
        s.log_request("t")
        s._con.commit()
        self.assertEqual(s.purge_usage(keep_days=90), 1)
        self.assertEqual(s.request_count("t"), 1)  # 今天的不动

    def test_migration_failure_rolls_back_and_retries(self):
        """迁移中途失败 → 整体回滚（老 schema 完好），修好数据后
        重试成功。Round 6 前：RENAME 后崩溃会导致数据孤儿在 docs_old。"""
        import hashlib
        import sqlite3

        db2 = os.path.join(self.tmp.name, "old.db")
        con = sqlite3.connect(db2)
        con.executescript("""
CREATE TABLE tenants(tenant_id TEXT PRIMARY KEY, api_key_hash TEXT UNIQUE,
  quota_docs INTEGER DEFAULT 10000, created_at REAL);
CREATE TABLE docs(tenant_id TEXT, doc_id TEXT PRIMARY KEY, text TEXT,
  metadata TEXT DEFAULT '{}', embedding BLOB, dim INTEGER, created_at REAL);
CREATE VIRTUAL TABLE docs_fts USING fts5(doc_id UNINDEXED, text, tokenize='trigram');
""")
        kh = hashlib.sha256(b"test-key-abc").hexdigest()
        con.execute("INSERT INTO tenants VALUES('t',?,10000,1)", (kh,))
        # 幽灵租户行：新 schema 有 FK 约束，迁移 INSERT 必炸
        con.execute("INSERT INTO docs VALUES('ghost','d1','x','{}',NULL,NULL,1)")
        con.execute("INSERT INTO docs_fts VALUES('d1','x')")
        con.commit()
        con.close()

        with self.assertRaises(Exception):
            TenantRAG(db2)
        # 回滚后老 schema 完好（用迁移自身的检测条件断言）
        con = sqlite3.connect(db2)
        cols = {r[1]: r[5] for r in con.execute("PRAGMA table_info(docs)")}
        self.assertEqual(cols.get("doc_id"), 1)  # 老库：doc_id 是全局主键
        self.assertEqual(cols.get("tenant_id"), 0)  # tenant_id 不在主键里
        self.assertEqual(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0], 1)
        con.execute("DELETE FROM docs WHERE tenant_id='ghost'")
        con.commit()
        con.close()
        # 重试成功
        rag2 = TenantRAG(db2)
        try:
            pk = [r[1] for r in
                  rag2.store._con.execute("PRAGMA table_info(docs)") if r[5] > 0]
            self.assertEqual(pk, ["tenant_id", "doc_id"])
        finally:
            rag2.close()


class ApiDemoTest(unittest.TestCase):
    """Round 7：Python/HTTP API 一致性、意图×币种、composer 截断。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rag = TenantRAG(
            os.path.join(self.tmp.name, "t.db"), embedder=FakeEmbedder()
        )
        self.rag.register("t", "test-api-key")
        self.rag.index("test-api-key", "腾讯视频 VIP 年费 198 元", doc_id="d1",
                       metadata={"name": "腾讯视频", "price_monthly": 16.5,
                                 "currency": "CNY"})
        self.rag.index("test-api-key", "ChatGPT Plus 20 dollars per month",
                       doc_id="d2",
                       metadata={"name": "ChatGPT Plus", "price_monthly": 20.0,
                                 "currency": "USD"})

    def tearDown(self):
        self.rag.close()
        self.tmp.cleanup()

    def _agent(self):
        from demo.sub_agent.agent import SubscriptionAgent

        return SubscriptionAgent(self.rag, "test-api-key")

    def test_usage_requests_today_parity(self):
        """Python usage() 与 HTTP /v1/usage 对齐：都有 requests_today。"""
        u = self.rag.usage("test-api-key")
        self.assertIn("requests_today", u)
        self.assertEqual(u["requests_today"], 0)
        self.rag.store.log_request("t")
        self.assertEqual(self.rag.usage("test-api-key")["requests_today"], 1)

    def test_currency_scoped_total(self):
        """显式币种限定必须对 total 生效（Round 7 前：问 USD 的 total
        会把 CNY 也算进去）。"""
        r = self._agent().ask("用美元结算的订阅一共多少钱")
        self.assertEqual(r["intent"], "total")
        self.assertNotIn("CNY", r["answer"])
        self.assertIn("20.00 USD", r["answer"])

    def test_currency_scoped_max(self):
        r = self._agent().ask("最贵的美元订阅是哪个")
        self.assertEqual(r["intent"], "max")
        self.assertIn("ChatGPT Plus", r["answer"])
        self.assertNotIn("腾讯视频", r["answer"])

    def test_currency_detect_eur_and_empty(self):
        from demo.sub_agent.intent import detect_currency

        self.assertEqual(detect_currency("用欧元结算的有哪些"), "EUR")
        r = self._agent().ask("用欧元结算的订阅一共多少钱")
        self.assertIn("EUR", r["answer"])
        self.assertEqual(r["sources"], [])

    def test_unscoped_total_unchanged(self):
        """无币种限定时行为不变：分币种汇总。"""
        r = self._agent().ask("我每个月一共花多少钱")
        self.assertIn("CNY", r["answer"])
        self.assertIn("USD", r["answer"])

    def test_intent_priority_deterministic(self):
        from demo.sub_agent.intent import detect_intent

        # 多意图同时命中时顺序确定：total > max > list > lookup
        self.assertEqual(detect_intent("我一共有多少个订阅，最贵的是哪个"), "total")
        self.assertEqual(detect_intent("列出最贵的订阅有哪些"), "max")
        self.assertEqual(detect_intent("一共有哪些订阅"), "total")

    def test_lookup_text_truncated_for_composer(self):
        """传给 composer 的文本截断到 500 字（+省略号），换成 LLM
        composer 不爆 token；sources 不受影响。"""
        from demo.sub_agent.agent import MAX_TEXT_CHARS

        long_text = "x" * 2000
        self.rag.index("test-api-key", long_text, doc_id="d-long",
                       metadata={"name": "超长文档"})
        seen = {}

        def spy_composer(question, hits):
            seen["texts"] = [h["text"] for h in hits]
            return "ok"

        from demo.sub_agent.agent import SubscriptionAgent

        ag = SubscriptionAgent(self.rag, "test-api-key", composer=spy_composer)
        r = ag.ask("x" * 10)
        self.assertEqual(r["intent"], "lookup")
        self.assertTrue(seen["texts"], "composer 应收到 hits")
        for t in seen["texts"]:
            self.assertLessEqual(len(t), MAX_TEXT_CHARS + 2)
        self.assertTrue(any(t.endswith("……") for t in seen["texts"]),
                        "被截断的文本应有省略号标记")
        self.assertIn("d-long", r["sources"])


if __name__ == "__main__":
    unittest.main()
