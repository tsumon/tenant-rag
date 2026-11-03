"""Evaluation tests include the provider opt-in path using only mocked clients."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eval import run_eval
from tenantrag.embed import FakeEmbedder


class EvaluationTest(unittest.TestCase):
    def _main(self, args, env=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with patch.dict(os.environ, env or {}, clear=True):
                code = run_eval.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_metrics_use_relevant_document_sets_and_truncated_rank(self):
        ranked = [("wrong", 0), ("right", 0), ("other", 0)]
        self.assertEqual(run_eval.recall_at_k(["right", "other"], ranked, 2), 0.5)
        self.assertEqual(run_eval.mrr(["right"], ranked), 0.5)
        self.assertEqual(run_eval.mrr(["right"], ranked[:1]), 0)
        self.assertEqual(run_eval.mrr(["absent"], ranked), 0)

    def test_default_fixture_has_distinct_suites_and_semantic_tags(self):
        docs, queries = run_eval.load_golden(run_eval.DEFAULT_GOLDEN)
        self.assertEqual(len(docs), 24)
        self.assertEqual(sum(q["suite"] == "pipeline" for q in queries), 16)
        semantic = [q for q in queries if q["suite"] == "semantic"]
        self.assertEqual(len(semantic), 14)
        tags = {tag for q in semantic for tag in q["tags"]}
        self.assertTrue({"cross-lingual", "paraphrase", "hard-negative", "negation"} <= tags)

    def test_offline_json_is_repeatable_and_never_calls_provider(self):
        with patch("tenantrag.embed.urllib.request.OpenerDirector.open", side_effect=AssertionError("network forbidden")):
            first = self._main(["--json", "--min-recall", "0.8", "--min-mrr", "0.7"])
            second = self._main(["--json", "--min-recall", "0.8", "--min-mrr", "0.7"])
        self.assertEqual(first, second)
        self.assertEqual(first[0], 0)
        report = json.loads(first[1])
        self.assertIn("unverified", report["semantic_quality"])
        self.assertEqual(report["backend"]["name"], "fake")
        self.assertEqual(report["gate"]["suite"], "pipeline")
        self.assertAlmostEqual(report["suites"]["pipeline"]["metrics"]["hybrid"]["recall_at_k"], 0.96875)
        self.assertEqual(report["suites"]["pipeline"]["metrics"]["hybrid"]["mrr_at_k"], 1)

    def test_gate_fail_has_nonzero_exit_and_keeps_report(self):
        code, stdout, _ = self._main(["--json", "--min-recall", "1"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(stdout)["gate"]["passed"])

    def test_text_output_labels_semantic_diagnostics(self):
        code, stdout, _ = self._main([])
        self.assertEqual(code, 0)
        self.assertIn("offline diagnostic only", stdout)
        self.assertIn("MRR@3", stdout)
        self.assertIn("No production-quality or hybrid-superiority claim", stdout)

    def test_json_output_matches_saved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.json"
            code, stdout, _ = self._main(["--json", "--output", str(target)])
            self.assertEqual(code, 0)
            self.assertEqual(target.read_text(), stdout)

    def test_invalid_cli_options_fail_before_network(self):
        cases = (["--k", "0"], ["--k", "-1"], ["--min-recall", "nan"],
                 ["--min-mrr", "inf"], ["--min-mrr", "1.1"],
                 ["--gate-suite", "semantic"], ["--suite", "semantic"],
                 ["--embedder", "openai"],
                 ["--embedder", "openai", "--allow-provider-requests"])
        with patch("tenantrag.embed.urllib.request.OpenerDirector.open") as call:
            for args in cases:
                with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                    self._main(args)
                self.assertEqual(error.exception.code, 2)
            call.assert_not_called()

    def test_explicit_provider_path_uses_environment_and_still_separates_suites(self):
        with patch.object(run_eval, "OpenAIEmbedder") as factory:
            factory.return_value = FakeEmbedder()
            code, stdout, stderr = self._main(
                ["--embedder", "openai", "--allow-provider-requests", "--json", "--gate-suite", "semantic"],
                {"EMBED_API_KEY": "dummy-provider-key", "EMBED_BASE_URL": "http://localhost:9999/v1",
                 "EMBED_MODEL": "test-model"})
            factory.assert_called_once_with(api_key="dummy-provider-key", base_url="http://localhost:9999/v1", model="test-model")
        self.assertEqual(code, 0)
        self.assertNotIn("dummy-provider-key", stdout + stderr)
        report = json.loads(stdout)
        self.assertEqual(report["backend"]["name"], "openai")
        self.assertEqual(report["gate"]["suite"], "semantic")
        self.assertIn("production quality unverified", report["semantic_quality"])

    def test_provider_requires_opt_in_even_with_key_present(self):
        with patch.object(run_eval, "OpenAIEmbedder") as factory:
            with self.assertRaises(SystemExit):
                self._main(["--embedder", "openai"], {"EMBED_API_KEY": "dummy-provider-key"})
            factory.assert_not_called()

    def test_provider_error_does_not_print_private_details_or_pass_gate(self):
        with patch.object(run_eval, "OpenAIEmbedder") as factory:
            factory.return_value.embed.side_effect = RuntimeError("secret-provider-body")
            code, stdout, stderr = self._main(
                ["--embedder", "openai", "--allow-provider-requests"], {"EMBED_API_KEY": "dummy-provider-key"})
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn("secret-provider-body", stderr)
        self.assertIn("no result or gate pass", stderr)

    def test_temporary_database_is_removed_even_when_embedding_fails(self):
        created = []
        original = tempfile.TemporaryDirectory

        def make_directory(*args, **kwargs):
            directory = original(*args, **kwargs)
            created.append(directory.name)
            return directory

        docs, queries = run_eval.load_golden(run_eval.DEFAULT_GOLDEN)
        with patch.object(run_eval.tempfile, "TemporaryDirectory", side_effect=make_directory):
            with patch.object(FakeEmbedder, "embed", side_effect=RuntimeError("broken")):
                with self.assertRaises(RuntimeError):
                    run_eval.evaluate(docs, queries, FakeEmbedder())
        self.assertTrue(created)
        self.assertTrue(all(not Path(path).exists() for path in created))

    def test_evaluation_calls_actual_hybrid_search(self):
        docs, queries = run_eval.load_golden(run_eval.DEFAULT_GOLDEN)
        with patch.object(run_eval, "hybrid_search", wraps=run_eval.hybrid_search) as search:
            run_eval.evaluate(docs, queries[:2], FakeEmbedder())
            self.assertEqual(search.call_count, 2)

    def test_malformed_fixture_is_rejected(self):
        doc = {"type": "doc", "doc_id": "d1", "text": "hello"}
        query = {"type": "query", "query": "hello", "expected": ["d1"]}
        cases = ([], [doc], [query], [doc, doc, query], [dict(doc, type="other"), query], [dict(doc, type=[]), query],
                 [dict(doc, text=""), query], [dict(doc, metadata=[]), query],
                 [doc, dict(query, expected=[])], [doc, dict(query, expected=["missing"])],
                 [doc, dict(query, expected=["d1", "d1"])], [doc, dict(query, expected=[None])],
                 [doc, dict(query, suite="unknown")], [doc, dict(query, tags="bad")], [doc, dict(query, query="")],
                 [doc, dict(query, id="same"), dict(query, id="same")])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.jsonl"
            for rows in cases:
                path.write_text("\n".join(json.dumps(row) for row in rows))
                with self.subTest(rows=rows), self.assertRaises(ValueError):
                    run_eval.load_golden(path)
            path.write_text("not-json")
            with self.assertRaisesRegex(ValueError, "line 1: invalid JSON"):
                run_eval.load_golden(path)

    def test_legacy_queries_default_to_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.jsonl"
            path.write_text('{"type":"doc","doc_id":"a","text":"hello"}\n'
                            '{"type":"query","query":"hello","expected":["a"]}\n')
            _, queries = run_eval.load_golden(path)
        self.assertEqual(queries[0]["suite"], "pipeline")
        self.assertEqual(queries[0]["id"], "q001")


if __name__ == "__main__":
    unittest.main()
