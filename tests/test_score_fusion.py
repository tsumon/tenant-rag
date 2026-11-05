"""Fusion invariants and API contracts use controlled vectors, not model scores."""
import contextlib
import io
import json
import math
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from eval import run_eval, compare_reports
from tenantrag import TenantRAG, AuthError
from tenantrag.embed import FakeEmbedder
from tenantrag.identity import EmbeddingIdentityError
from tenantrag.search import score_fusion, rrf, validate_fusion
from tenantrag.server import make_server


class ScoreFormulaTest(unittest.TestCase):
    def test_score_preserves_a_strong_semantic_margin(self):
        vector = [('semantic', 0.9), ('weak_keyword', 0.2)]
        self.assertEqual(rrf([['semantic', 'weak_keyword'], ['weak_keyword']])[0][0], 'weak_keyword')
        self.assertEqual(score_fusion(vector, ['weak_keyword'])[0][0], 'semantic')

    def test_lexical_bonus_can_rescue_a_close_candidate(self):
        hits = score_fusion([('other', 0.6), ('exact', 0.5)], ['exact'])
        self.assertEqual(hits[0][0], 'exact')
        self.assertAlmostEqual(hits[0][1], 0.7)

    def test_bonus_is_bounded_and_duplicate_fts_hits_do_not_multiply_it(self):
        result = dict(score_fusion([('a', 0.4), ('b', 0.3)], ['a', 'a', 'b'], 0.2))
        self.assertAlmostEqual(result['a'], 0.6)
        self.assertAlmostEqual(result['b'], 0.4)

    def test_lexical_only_and_vector_only_candidates(self):
        self.assertEqual(score_fusion([], ['a', 'b'], 0.2), [('a', 0.2), ('b', 0.1)])
        self.assertEqual(score_fusion([('a', 0.7)], [], 0.2), [('a', 0.7)])
        self.assertEqual(score_fusion([], [], 0.2), [])

    def test_zero_weight_is_positive_vector_only(self):
        self.assertEqual(score_fusion([('a', 0.5), ('zero', 0), ('negative', -0.3)], ['missing', 'a'], 0), [('a', 0.5)])
        self.assertEqual(score_fusion([], ['lexical'], 0), [])

    def test_ties_keep_vector_then_keyword_order(self):
        self.assertEqual([x[0] for x in score_fusion([('a', 0.3), ('b', 0.3)], [], 0)], ['a', 'b'])
        self.assertEqual([x[0] for x in score_fusion([('a', 0.2)], ['b'], 0.2)], ['a', 'b'])

    def test_invalid_fusion_settings_are_rejected(self):
        for mode in (None, [], {}, True, 'unknown', 'SCORE'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_fusion(mode)
        for weight in (True, False, [], {}, '0.2', -0.1, 1.01, float('nan'), float('inf'), 10**1000):
            with self.subTest(weight=str(weight)[:30]), self.assertRaises(ValueError):
                validate_fusion('score', weight)
        with self.assertRaises(ValueError):
            validate_fusion('rrf', 0.2)
        self.assertIsNone(validate_fusion('rrf'))
        self.assertEqual(validate_fusion('score'), 0.2)


class ControlledEmbedder:
    dim = 2
    embedding_identity = {'provider': 'fixture', 'model': 'controlled-vectors', 'revision': '1'}
    def embed(self, texts):
        return [[0.2, math.sqrt(0.96)] if text == 'archive catalogue' else [1.0, 0.0] for text in texts]


class ScoreServiceTest(unittest.TestCase):
    def setUp(self):
        self.rag = TenantRAG(':memory:', embedder=ControlledEmbedder())
        self.rag.register('a', 'test-key-a')
        self.rag.register('b', 'test-key-b')
        self.rag.index('test-key-a', '删除档案的流程', doc_id='shared')
        self.rag.index('test-key-a', 'archive catalogue', doc_id='keyword')
        self.rag.index('test-key-b', 'private archive from another tenant', doc_id='shared')
        self.addCleanup(self.rag.close)

    def test_legacy_default_and_explicit_rrf_remain_identical(self):
        self.assertEqual(self.rag.search('test-key-a', 'archive'), self.rag.search('test-key-a', 'archive', fusion='rrf'))
        self.assertEqual(self.rag.search('test-key-a', 'archive')[0]['doc_id'], 'keyword')

    def test_explicit_score_fusion_keeps_cross_language_semantic_hit(self):
        hits = self.rag.search('test-key-a', 'archive', fusion='score')
        self.assertEqual(hits[0]['doc_id'], 'shared')
        self.assertEqual(hits[0]['text'], '删除档案的流程')
        self.assertEqual(hits[0]['matched'], ['vector'])
        self.assertEqual(hits[1]['matched'], ['vector', 'fts'])
        self.assertLessEqual(len(self.rag.search('test-key-a', 'archive', k=1, fusion='score')), 1)

    def test_same_ids_and_other_tenant_keywords_do_not_leak(self):
        for weight in (0, 0.2, 1):
            hits = self.rag.search('test-key-a', 'private archive', fusion='score', lexical_weight=weight)
            self.assertTrue(all('another tenant' not in hit['text'] for hit in hits))
            self.assertEqual(self.rag.search('test-key-b', 'private archive', fusion='score')[0]['text'], 'private archive from another tenant')

    def test_invalid_settings_fail_before_embedding(self):
        with patch.object(self.rag.embedder, 'embed') as call:
            with self.assertRaises(ValueError):
                self.rag.search('test-key-a', 'archive', fusion='score', lexical_weight=float('nan'))
            with self.assertRaises(ValueError):
                self.rag.search('test-key-a', 'archive', fusion='rrf', lexical_weight=0.2)
            call.assert_not_called()

    def test_authentication_is_not_bypassed_by_fusion_settings(self):
        with patch.object(self.rag.embedder, 'embed') as call:
            with self.assertRaises(AuthError):
                self.rag.search('wrong-key', 'archive', fusion='score')
            call.assert_not_called()

    def test_model_identity_still_fails_before_embedding(self):
        self.rag.embedder.embedding_identity = {**ControlledEmbedder.embedding_identity, 'revision': 'changed'}
        with patch.object(self.rag.embedder, 'embed') as call:
            with self.assertRaises(EmbeddingIdentityError):
                self.rag.search('test-key-a', 'archive', fusion='score')
            call.assert_not_called()

    def test_zero_weight_does_not_query_fts(self):
        with patch.object(self.rag.store, 'fts_search') as call:
            hits = self.rag.search('test-key-a', 'archive', fusion='score', lexical_weight=0)
            call.assert_not_called()
        self.assertEqual(hits[0]['doc_id'], 'shared')
        self.assertTrue(all(hit['matched'] == ['vector'] for hit in hits))

    def test_fts_syntax_is_still_safely_tokenized(self):
        hits = self.rag.search('test-key-a', 'archive AND "OR" NOT (DROP TABLE docs)', fusion='score')
        self.assertTrue(hits)
        self.assertEqual(self.rag.usage('test-key-a')['docs'], 2)


class ScoreHTTPTest(unittest.TestCase):
    def setUp(self):
        self.server = make_server(':memory:', port=0, rate_capacity=1000)
        self.server.rag.register('a', 'test-key-a')
        self.server.rag.register('b', 'test-key-b')
        self.server.rag.ingest('test-key-a', 'alpha beta gamma delta', doc_id='guide', max_chars=10, overlap=2)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close(); self.server.rag.close()

    def request(self, body, key='test-key-a'):
        req = urllib.request.Request(f'http://127.0.0.1:{self.server.server_address[1]}/v1/search',
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_score_http_preserves_chunk_metadata_and_tenant_scope(self):
        status, result = self.request({'query': 'gamma', 'fusion': 'score', 'lexical_weight': 0.2})
        self.assertEqual(status, 200)
        self.assertEqual(result['hits'][0]['metadata']['_chunk']['parent_id'], 'guide')
        self.assertEqual(self.request({'query': 'gamma', 'fusion': 'score'}, key='test-key-b')[1]['hits'], [])
        self.assertEqual(self.request({'query': 'gamma', 'fusion': 'score'}, key='bad')[0], 401)

    def test_http_invalid_fusion_returns_400_without_embedding(self):
        with patch.object(self.server.rag.embedder, 'embed') as call:
            for settings in ({'fusion': []}, {'fusion': None}, {'fusion': 'unknown'},
                             {'fusion': 'score', 'lexical_weight': True},
                             {'fusion': 'score', 'lexical_weight': '0.2'},
                             {'fusion': 'score', 'lexical_weight': -1},
                             {'fusion': 'score', 'lexical_weight': 2},
                             {'lexical_weight': 0.2}):
                with self.subTest(settings=settings):
                    self.assertEqual(self.request({'query': 'gamma', **settings})[0], 400)
            call.assert_not_called()


class ScoreEvaluationTest(unittest.TestCase):
    def report(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(run_eval.main(['--json', *args]), 0)
        return json.loads(output.getvalue())

    def test_zero_weight_hybrid_equals_vector_rankings_and_metrics(self):
        report = self.report(['--fusion', 'score', '--lexical-weight', '0'])
        for query in report['queries']:
            self.assertEqual(query['ranked']['hybrid'], query['ranked']['vector'])
            self.assertEqual(query['metrics']['hybrid'], query['metrics']['vector'])

    def test_report_and_dry_run_record_applied_fusion_settings(self):
        report = self.report(['--fusion', 'score'])
        self.assertEqual(report['retrieval']['lexical_weight'], 0.2)
        self.assertEqual(report['retrieval']['fusion'], 'score')
        self.assertIsNone(report['retrieval']['rrf_k'])
        plan = self.report(['--fusion', 'score', '--lexical-weight', '0.5', '--dry-run'])
        self.assertEqual(plan['lexical_weight'], 0.5)

    def test_invalid_settings_fail_before_provider_construction(self):
        with patch.object(run_eval, 'OpenAIEmbedder') as factory:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                run_eval.main(['--embedder', 'openai', '--allow-provider-requests', '--lexical-weight', '0.2'])
            factory.assert_not_called()

    def test_fusion_comparison_requires_explicit_intent(self):
        baseline, candidate = self.report([]), self.report(['--fusion', 'score'])
        with self.assertRaises(ValueError):
            compare_reports.compare_reports(baseline, candidate)
        result = compare_reports.compare_reports(baseline, candidate, allow_fusion_change=True)
        self.assertEqual(result['comparison_kind'], 'fusion')
        self.assertEqual(result['retrieval_settings']['candidate']['fusion'], 'score')

    def test_fusion_comparison_cannot_also_change_model_or_chunking(self):
        baseline = self.report([])
        for changed in ('backend', 'runtime', 'chunking'):
            candidate = self.report(['--fusion', 'score'])
            if changed == 'backend':
                candidate['backend']['revision'] = 'different'
            elif changed == 'runtime':
                candidate['python_version'] = 'different'
            else:
                candidate['retrieval']['chunking'] = True
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                compare_reports.compare_reports(baseline, candidate, allow_fusion_change=True)

    def test_fusion_comparison_rejects_changed_or_missing_candidate_inputs(self):
        baseline = self.report([])
        for kind in ('changed', 'missing'):
            candidate = self.report(['--fusion', 'score'])
            if kind == 'changed':
                candidate['queries'][0]['candidate_sha256']['vector'] = '0' * 64
            else:
                del candidate['queries'][0]['candidate_sha256']
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                compare_reports.compare_reports(baseline, candidate, allow_fusion_change=True)


if __name__ == '__main__':
    unittest.main()
