"""New HTTP routes and stable old index contract."""
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from tenantrag.server import make_server

class IngestHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server(self.tmp.name + '/test.db', port=0, rate_capacity=100)
        self.server.rag.register('a', 'tenant-key-a', quota_docs=2)
        self.server.rag.register('b', 'tenant-key-b', quota_docs=2)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()
        self.server.rag.close(); self.tmp.cleanup()

    def call(self, path, body=None, method='POST', key='tenant-key-a'):
        req = urllib.request.Request('http://127.0.0.1:%s%s' % (self.server.server_address[1], path),
            data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={'Authorization':'Bearer ' + key, 'Content-Type':'application/json'})
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_ingest_parent_lifecycle(self):
        payload = {'doc_id':'guide','text':'alpha beta gamma delta','max_chars':10,'overlap':2,'source':'guide.md'}
        code, body = self.call('/v1/ingest', payload)
        self.assertEqual(code, 200); self.assertGreater(body['chunks'], 1)
        self.assertTrue(self.call('/v1/ingest', payload)[1]['unchanged'])
        hits = self.call('/v1/search', {'query':'gamma'})[1]['hits']
        self.assertEqual(hits[0]['metadata']['_chunk']['parent_id'], 'guide')
        self.assertEqual(self.call('/v1/docs', method='GET')[1]['docs'][0]['doc_id'], 'guide')
        self.assertEqual(self.call('/v1/search', {'query':'gamma'}, key='tenant-key-b')[1]['hits'], [])
        self.assertEqual(self.call('/v1/docs/guide', method='DELETE')[1], {'deleted':True})
        self.assertEqual(self.call('/v1/search', {'query':'gamma'})[1]['hits'], [])

    def test_batch_quota_bad_input_and_old_index(self):
        code, body = self.call('/v1/ingest/batch', {'documents':[
            {'doc_id':str(i),'text':'document '+str(i)} for i in range(3)]})
        self.assertEqual(code,403)
        self.assertEqual(self.call('/v1/docs', method='GET')[1]['docs'], [])
        self.assertEqual(self.call('/v1/ingest', {'text':'x','max_chars':4,'overlap':4})[0],400)
        self.assertEqual(self.call('/v1/index', {'doc_id':'old','text':'old interface'})[1], {'doc_id':'old'})

    def test_legacy_identity_conflict(self):
        self.server.rag.store.upsert_doc('a','legacy','legacy text',embedding=[1.0,0.0])
        self.assertEqual(self.call('/v1/search', {'query':'legacy'})[0],409)
        self.assertEqual(self.call('/v1/docs', method='GET')[0],200)
