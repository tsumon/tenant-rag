import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from eval import run_eval, compare_reports


class CompareReportsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            run_eval.main(['--json'])
        cls.report = json.loads(output.getvalue())

    def test_self_comparison_is_zero(self):
        result = compare_reports.compare_reports(self.report, self.report)
        self.assertEqual(result['changed_queries'], [])
        self.assertEqual(result['unchanged_metric_queries'], 30)
        self.assertEqual(result['suite_deltas']['semantic']['hybrid']['recall_at_k'], 0)

    def test_candidate_deltas_and_changed_rankings(self):
        changed = deepcopy(self.report)
        query = changed['queries'][0]
        query['metrics']['hybrid']['recall_at_k'] = 0
        query['metrics']['hybrid']['mrr_at_k'] = 0
        query['ranked']['hybrid'] = []
        changed['suites']['pipeline']['metrics']['hybrid']['recall_at_k'] -= 1 / 16
        changed['suites']['pipeline']['metrics']['hybrid']['mrr_at_k'] -= 1 / 16
        result = compare_reports.compare_reports(self.report, changed)
        self.assertEqual(len(result['changed_queries']), 1)
        self.assertEqual(result['changed_queries'][0]['delta']['hybrid']['recall_at_k'], -1)
        self.assertEqual(result['suite_deltas']['pipeline']['hybrid']['recall_at_k'], -1 / 16)

    def test_mismatched_corpus_selection_or_settings_fail_closed(self):
        for field, value in [('k', 5), ('candidates_per_channel', 100),
                             ('retrieval', {}), ('implementation_sha256', {'other': 'x'})]:
            changed = deepcopy(self.report)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                compare_reports.compare_reports(self.report, changed)
        for field in ('sha256', 'selected_records_sha256'):
            changed = deepcopy(self.report)
            changed['dataset'][field] = 'b' * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                compare_reports.compare_reports(self.report, changed)

    def test_changed_labels_duplicate_ids_invalid_metrics_rejected(self):
        for kind in ('labels', 'duplicate', 'nonfinite', 'missing', 'version', 'metrics', 'aggregate', 'ranking'):
            changed = deepcopy(self.report)
            if kind == 'labels':
                changed['queries'][0]['expected'] = ['other']
            elif kind == 'duplicate':
                changed['queries'].append(changed['queries'][0])
            elif kind == 'nonfinite':
                changed['queries'][0]['metrics']['hybrid']['mrr_at_k'] = float('nan')
            elif kind == 'missing':
                del changed['suites']
            elif kind == 'metrics':
                changed['queries'][0]['metrics']['hybrid']['mrr_at_k'] = 0
            elif kind == 'aggregate':
                changed['suites']['pipeline']['metrics']['hybrid']['mrr_at_k'] = 0
            elif kind == 'ranking':
                changed['queries'][0]['ranked']['hybrid'] = ['same', 'same']
            else:
                changed['schema_version'] = 1
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                compare_reports.compare_reports(self.report, changed)

    def test_runtime_change_is_reported_not_hidden(self):
        changed = deepcopy(self.report)
        changed['python_version'] = 'different'
        result = compare_reports.compare_reports(self.report, changed)
        self.assertEqual(result['runtime_changes']['python_version']['candidate'], 'different')

    def test_cli_saved_output_and_malformed_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'report.json', Path(tmp) / 'comparison.json'
            source.write_text(json.dumps(self.report))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(compare_reports.main([str(source), str(source), '--output', str(target)]), 0)
            self.assertEqual(output.getvalue(), target.read_text())
            source.write_text('private malformed contents')
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                self.assertEqual(compare_reports.main([str(source), str(source)]), 2)
            self.assertNotIn('private', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
