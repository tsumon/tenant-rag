"""Example mechanics only; fake embeddings here are NOT model-quality evidence."""
from pathlib import Path
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_trial import ROOT, check_output_paths, citation, evaluate_query, load_data, run_scenario
from tenantrag.embed import FakeEmbedder


class TrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents, cls.queries = load_data(ROOT / 'data')

    def test_complete_scenario_invariants_with_fake_only(self):
        report = run_scenario(FakeEmbedder(), self.documents, self.queries)
        self.assertEqual(len(report['queries']), 16)
        self.assertEqual(report['checks']['other_key_query_runs'], 32)
        self.assertTrue(all(v for v in report['checks'].values()))
        for tenant in ('aurora', 'cedar'):
            self.assertEqual(report['ingestion'][tenant]['parents'], 5)
            self.assertEqual(report['ingestion'][tenant]['unchanged_retry_extra_embedding_calls'], 0)

    def test_parent_hit_is_not_automatically_evidence_hit(self):
        query = {'relevant': [{'doc_id': 'refunds', 'evidence': 'Actual policy'}]}
        metrics = evaluate_query(query, [{'parent_id': 'refunds', 'text': 'Unrelated heading'}])
        self.assertEqual(metrics['parent_recall_at_3_chunks'], 1)
        self.assertEqual(metrics['exact_evidence_recall_at_3_chunks'], 0)

    def test_chunk_rank_is_not_deduplicated_for_mrr(self):
        query = {'relevant': [{'doc_id': 'refunds', 'evidence': 'Actual policy'}]}
        hits = [{'parent_id': 'other', 'text': 'a'}, {'parent_id': 'other', 'text': 'b'},
                {'parent_id': 'refunds', 'text': 'Actual policy'}]
        self.assertAlmostEqual(evaluate_query(query, hits)['parent_reciprocal_rank_at_3_chunks'], 1 / 3)

    def test_multiple_relevant_parents(self):
        query = {'relevant': [{'doc_id': 'a', 'evidence': 'One'}, {'doc_id': 'b', 'evidence': 'Two'}]}
        result = evaluate_query(query, [{'parent_id': 'a', 'text': 'One'}])
        self.assertEqual(result['parent_recall_at_3_chunks'], 0.5)
        self.assertEqual(result['exact_evidence_recall_at_3_chunks'], 0.5)

    def test_out_of_scope_has_no_artificial_recall(self):
        result = evaluate_query({'relevant': []}, [{'parent_id': 'a', 'text': 'irrelevant'}])
        self.assertEqual(result, {'out_of_scope_returned_chunks': 1})

    def make_hit(self):
        doc = self.documents['aurora'][0]
        return {'metadata': {'tenant': 'aurora', '_chunk': {'parent_id': doc['doc_id'],
            'source': doc['source'], 'start': 0, 'end': 25}}, 'text': doc['text'][:25],
            'score': 0.1, 'matched': ['vector']}

    def test_exact_citation(self):
        hit = self.make_hit()
        self.assertEqual(citation(hit, 'aurora', self.documents)['text'], hit['text'])

    def test_wrong_tenant_citation_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'Cross-tenant'):
            citation(self.make_hit(), 'cedar', self.documents)

    def test_modified_excerpt_fails(self):
        hit = self.make_hit()
        hit['text'] += 'invented policy'
        with self.assertRaisesRegex(RuntimeError, 'exact source'):
            citation(hit, 'aurora', self.documents)

    def test_wrong_source_fails(self):
        hit = self.make_hit()
        hit['metadata']['_chunk']['source'] = 'cedar/other.md'
        with self.assertRaisesRegex(RuntimeError, 'source'):
            citation(hit, 'aurora', self.documents)

    def test_bad_offsets_fail(self):
        for value in (-1, True, 100000):
            hit = self.make_hit()
            hit['metadata']['_chunk']['start'] = value
            with self.assertRaisesRegex(RuntimeError, 'offset'):
                citation(hit, 'aurora', self.documents)

    def test_frozen_data_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'SHA256SUMS').write_text('0' * 64 + '  queries.json\n')
            (directory / 'queries.json').write_text('[]')
            with self.assertRaisesRegex(RuntimeError, 'hash mismatch'):
                load_data(directory)

    def test_unlisted_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'data'
            shutil.copytree(ROOT / 'data', directory)
            (directory / 'aurora' / 'extra.md').write_text('Unreviewed source')
            with self.assertRaisesRegex(RuntimeError, 'file set mismatch'):
                load_data(directory)

    def test_existing_timing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'report.json'
            output.with_suffix('.timing.json').write_text('preserve')
            with self.assertRaisesRegex(RuntimeError, 'timing output exists'):
                check_output_paths(output)

    def test_existing_report_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'report.json'
            output.write_text('preserve')
            with self.assertRaisesRegex(RuntimeError, 'output exists'):
                check_output_paths(output)

    def test_fresh_output_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            check_output_paths(Path(temp) / 'report.json')


if __name__ == '__main__':
    unittest.main()
