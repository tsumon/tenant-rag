"""Release regression tests; no external network or provider calls."""
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from admin.admin_server import AdminHandler
from tenantrag import TenantRAG
from tenantrag.server import make_server
from tests.test_migration import OLD_SCHEMA


class ReleaseTest(unittest.TestCase):
    def test_invalid_cli_rate_exits_before_database_creation(self):
        import contextlib
        import io
        from tenantrag.server import main
        with patch("sys.argv", ["tenant-rag", "--rate", "0"]), \
             patch("tenantrag.server.make_server") as create, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main()
            self.assertEqual(caught.exception.code, 2)
            create.assert_not_called()

    def test_context_manager_closes_database(self):
        with TenantRAG(':memory:') as rag:
            rag.register('a', 'test-key-a')
        with self.assertRaises(sqlite3.ProgrammingError):
            rag.store.doc_count('a')

    def test_migration_preserves_tenant_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + '/legacy.db'
            con = sqlite3.connect(path)
            con.executescript(OLD_SCHEMA)
            con.execute('CREATE INDEX idx_docs_tenant ON docs(tenant_id)')
            con.close()
            with TenantRAG(path) as rag:
                names = [r[1] for r in rag.store._con.execute('PRAGMA index_list(docs)')]
                self.assertIn('idx_docs_tenant', names)

    def test_invalid_rate_configuration(self):
        for kwargs in ({'rate_capacity': 0}, {'rate_capacity': -1},
                       {'rate_per_seconds': -1}, {'rate_capacity': True}):
            with self.assertRaises(ValueError):
                make_server(':memory:', port=0, **kwargs)


class AdminReleaseTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), AdminHandler)
        self.server.api_base = 'http://127.0.0.1:1'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, length):
        con = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            con.request('POST', path, headers={'Content-Length': str(length)})
            response = con.getresponse()
            return response.status, json.loads(response.read())
        finally:
            con.close()

    def test_invalid_lengths_rejected_without_read_or_upstream(self):
        with patch('urllib.request.urlopen') as upstream:
            for size in (-1, 0, 10485761, 'invalid'):
                status, body = self.request('/v1/index', size)
                self.assertEqual(status, 400)
            upstream.assert_not_called()

    def test_similar_route_is_not_allowlisted(self):
        status, _ = self.request('/v1/index-unintended', 0)
        self.assertEqual(status, 404)

    def test_retry_after_preserved(self):
        import io
        import urllib.error
        error = urllib.error.HTTPError('http://localhost', 429, 'rate',
                                       {'Retry-After': '7'}, io.BytesIO(b'{"error":"rate_limited"}'))
        with patch('urllib.request.urlopen', side_effect=error):
            con = http.client.HTTPConnection(*self.server.server_address, timeout=2)
            try:
                con.request('GET', '/v1/usage')
                response = con.getresponse()
                self.assertEqual(response.status, 429)
                self.assertEqual(response.getheader('Retry-After'), '7')
                response.read()
            finally:
                con.close()
