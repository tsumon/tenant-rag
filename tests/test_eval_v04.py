import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eval import run_eval
from tenantrag.embed import FakeEmbedder


class ReproducibleEvaluationTest(unittest.TestCase):
    corpus = Path(run_eval.__file__).with_name('operations-v1.jsonl')
    manifest = corpus.with_suffix('.manifest.json')

    def test_operations_corpus_has_rationales_and_verified_provenance(self):
        docs, queries = run_eval.load_golden(self.corpus)
        result = run_eval.dataset_manifest(self.corpus, docs, queries, self.manifest)
        self.assertEqual(result['manifest']['version'], '1')
        self.assertEqual(len(docs), 12)
        self.assertEqual(len(queries), 13)
        self.assertTrue(all(q['label_rationale'] for q in queries))
        self.assertTrue(any(len(q['expected']) > 1 for q in queries))
        subset = run_eval.dataset_manifest(self.corpus, docs, queries[:2])
        self.assertEqual(result['sha256'], subset['sha256'])
        self.assertNotEqual(result['selected_records_sha256'], subset['selected_records_sha256'])

    def test_modified_corpus_is_rejected_before_embedder_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            changed = Path(tmp) / 'changed.jsonl'
            changed.write_bytes(self.corpus.read_bytes() + b'\n')
            with patch.object(run_eval, 'OpenAIEmbedder') as factory:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    run_eval.main(['--golden', str(changed), '--manifest', str(self.manifest),
                                   '--embedder', 'openai', '--allow-provider-requests'])
                factory.assert_not_called()

    def test_provider_plan_requires_no_credentials_or_opt_in_and_makes_no_calls(self):
        with patch.object(run_eval, 'OpenAIEmbedder') as factory, patch.object(run_eval, 'evaluate') as evaluate:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = run_eval.main(['--golden', str(self.corpus), '--manifest', str(self.manifest),
                                      '--embedder', 'openai', '--suite', 'semantic',
                                      '--gate-suite', 'semantic', '--dry-run'])
            self.assertEqual(code, 0)
            plan = json.loads(output.getvalue())
            self.assertEqual(plan['planned_embedding_requests'], 23)
            self.assertEqual(plan['provider_requests_made'], 0)
            self.assertNotIn('gate', plan)
            factory.assert_not_called()
            evaluate.assert_not_called()

    def test_provider_revision_is_forwarded_and_reported_without_exposing_endpoint(self):
        output = io.StringIO()
        with patch.object(run_eval, 'OpenAIEmbedder', return_value=FakeEmbedder()) as factory:
            with patch.dict('os.environ', {'EMBED_API_KEY': 'dummy-test-key'}, clear=True):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    code = run_eval.main(['--embedder', 'openai', '--allow-provider-requests',
                        '--revision', 'deployment-v2', '--base-url', 'http://localhost:7777/private', '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(factory.call_args.kwargs['revision'], 'deployment-v2')
        backend = json.loads(output.getvalue())['backend']
        self.assertEqual(backend['revision'], 'deployment-v2')
        self.assertEqual(len(backend['endpoint_sha256']), 64)
        self.assertNotIn('http://localhost:7777/private', output.getvalue())
        self.assertNotIn('dummy-test-key', output.getvalue())

    def test_tag_breakdown_uses_only_members_and_fingerprint_is_stable(self):
        docs, queries = run_eval.load_golden(self.corpus)
        report = run_eval.evaluate(docs, queries, FakeEmbedder())
        self.assertEqual(report['tags']['multi-relevant']['queries'], 1)
        target = next(q for q in report['queries'] if 'multi-relevant' in q['tags'])
        self.assertEqual(report['tags']['multi-relevant']['metrics'], target['metrics'])
        self.assertEqual(run_eval.implementation_fingerprint(), run_eval.implementation_fingerprint())


if __name__ == '__main__':
    unittest.main()
