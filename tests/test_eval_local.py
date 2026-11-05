"""Local-model adapter contract tests use mocks, not pretrained-model scores."""
import contextlib
import builtins
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from eval import run_eval
from eval.local_embedder import LocalModelError, LocalSentenceTransformer, model_fingerprint
from tenantrag.embed import FakeEmbedder


class LocalEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "model"
        self.path.mkdir()
        (self.path / "modules.json").write_text(json.dumps([
            {"idx": 0, "name": "0", "path": "", "type": "sentence_transformers.models.Transformer"},
            {"idx": 1, "name": "1", "path": "1_Pooling", "type": "sentence_transformers.models.Pooling"}]))
        (self.path / '1_Pooling').mkdir()
        (self.path / '1_Pooling/config.json').write_text('{"word_embedding_dimension": 2}')
        (self.path / "model.safetensors").write_bytes(b"test fixture, not real model weights")
        self.model = MagicMock()
        self.model.get_sentence_embedding_dimension.return_value = 2
        self.model.max_seq_length = 128
        self.model.encode.return_value.tolist.return_value = [[1.0, 0.0]]
        self.factory = MagicMock(return_value=self.model)
        self.layers = types.SimpleNamespace(Transformer=MagicMock(), Pooling=MagicMock(), Normalize=MagicMock())
        self.module = types.SimpleNamespace(SentenceTransformer=self.factory, models=self.layers)

    def adapter(self):
        with patch.dict(sys.modules, {"sentence_transformers": self.module}):
            return LocalSentenceTransformer(self.path)

    def test_hash_is_location_independent_and_tracks_weight_changes(self):
        first = model_fingerprint(self.path)
        copy = Path(self.tmp.name) / "copy"
        shutil.copytree(self.path, copy)
        self.assertEqual(first, model_fingerprint(copy))
        (copy / "model.safetensors").write_bytes(b"different fixture")
        self.assertNotEqual(first, model_fingerprint(copy))
        self.assertEqual(first["file_count"], 3)

    def test_hash_tracks_relative_filenames_and_configuration(self):
        first = model_fingerprint(self.path)
        (self.path / "config.json").write_text('{}')
        second = model_fingerprint(self.path)
        self.assertNotEqual(first, second)
        (self.path / "config.json").rename(self.path / "renamed.json")
        self.assertNotEqual(second, model_fingerprint(self.path))

    def test_snapshot_symlink_files_are_hashed(self):
        original = model_fingerprint(self.path)
        blob = Path(self.tmp.name) / "blob"
        weight = self.path / "model.safetensors"
        weight.rename(blob)
        weight.symlink_to(blob)
        self.assertEqual(original, model_fingerprint(self.path))

    def test_directory_symlinks_cannot_hide_weights_or_custom_code(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (outside / 'model.safetensors').write_bytes(b'external fixture')
        (outside / 'pytorch_model.bin').write_bytes(b'unsupported fixture')
        (self.path / 'encoder').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(LocalModelError, 'directory symlinks'):
            model_fingerprint(self.path)

    def test_missing_path_or_model_id_never_loads(self):
        with patch.dict(sys.modules, {"sentence_transformers": self.module}):
            with self.assertRaisesRegex(LocalModelError, "not downloaded"):
                LocalSentenceTransformer(Path(self.tmp.name) / "nonexistent")
        self.factory.assert_not_called()

    def test_custom_code_and_pickle_weights_are_rejected(self):
        for filename in ('weights.bin', 'weights.pt', 'weights.pkl', 'model.py'):
            with self.subTest(filename=filename):
                target = self.path / filename
                target.write_bytes(b'fixture')
                with self.assertRaisesRegex(LocalModelError, "safetensors-only"):
                    model_fingerprint(self.path)
                target.unlink()
        (self.path / "model.safetensors").unlink()
        with self.assertRaisesRegex(LocalModelError, "requires safetensors"):
            model_fingerprint(self.path)

    def test_unsupported_modules_and_traversal_are_rejected(self):
        for module in ({"type": "evil.Custom", "path": ""},
                       {"type": "sentence_transformers.models.Dense", "path": ""},
                       {"type": "sentence_transformers.models.Pooling", "path": "../other"},
                       {"type": "sentence_transformers.models.Pooling", "path": "/other"},
                       None):
            (self.path / "modules.json").write_text(json.dumps([module]))
            with self.subTest(module=module), self.assertRaises(LocalModelError):
                model_fingerprint(self.path)

    def test_missing_dependencies_give_install_hint(self):
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            with self.assertRaisesRegex(LocalModelError, "local-eval"):
                LocalSentenceTransformer(self.path)

    def test_external_tokenizer_or_adapter_or_shard_paths_are_rejected(self):
        cases = [('sentence_bert_config.json', {'tokenizer_name_or_path': '/outside'}),
                 ('adapter_config.json', {'base_model_name_or_path': 'remote/model'}),
                 ('config_sentence_transformers.json', {'prompts': {'query': 'query: '}}),
                 ('tokenizer_config.json', {'tokenizer_file': '/outside/tokenizer.json'}),
                 ('tokenizer_config.json', {'vocab_file': '../vocab.txt'}),
                 ('tokenizer_config.json', {'fast_tokenizer_files': ['tokenizer.json']}),
                 ('model.safetensors.index.json', {'weight_map': {'a': '../outside.safetensors'}}),
                 ('model.safetensors.index.json', {'weight_map': {'a': '.git/hidden.safetensors'}})]
        for name, content in cases:
            target = self.path / name
            target.write_text(json.dumps(content))
            with self.subTest(name=name), self.assertRaises(LocalModelError):
                model_fingerprint(self.path)
            target.unlink()

    def test_nonroot_transformer_is_rejected(self):
        target = self.path / 'modules.json'
        layout = json.loads(target.read_text())
        layout[0]['path'] = 'encoder'
        target.write_text(json.dumps(layout))
        with self.assertRaises(LocalModelError):
            model_fingerprint(self.path)

    def test_dependency_runtime_error_is_sanitized(self):
        original = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == 'sentence_transformers':
                raise RuntimeError('private model path and native library details')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=importing):
            with self.assertRaisesRegex(LocalModelError, 'dependency import failed') as caught:
                LocalSentenceTransformer(self.path)
        self.assertNotIn('private', str(caught.exception))

    def test_loading_has_no_remote_download_or_code_fallback(self):
        adapter = self.adapter()
        self.layers.Transformer.assert_called_once_with(str(self.path.resolve()),
            model_args={'local_files_only': True, 'trust_remote_code': False, 'use_safetensors': True},
            tokenizer_args={'local_files_only': True, 'trust_remote_code': False},
            config_args={'local_files_only': True, 'trust_remote_code': False})
        self.factory.assert_called_once_with(modules=[self.layers.Transformer.return_value,
            self.layers.Pooling.return_value], device='cpu')
        self.assertEqual(adapter.embedding_identity['revision'], model_fingerprint(self.path)['sha256'])
        self.assertEqual(adapter.settings["prompt"], "")

    def test_load_failure_is_sanitized_and_does_not_retry(self):
        self.factory.side_effect = RuntimeError("private contents and /private/path")
        with self.assertRaises(LocalModelError) as caught:
            self.adapter()
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.factory.call_count, 1)

    def test_changed_weights_during_load_are_rejected(self):
        def change(*args, **kwargs):
            (self.path / "model.safetensors").write_bytes(b'changed during loading')
            return self.model
        self.factory.side_effect = change
        with self.assertRaises(LocalModelError):
            self.adapter()

    def test_embedding_contract_explicit_prompt_normalization_and_empty_batch(self):
        adapter = self.adapter()
        self.assertEqual(adapter.embed([]), [])
        self.model.encode.assert_not_called()
        self.assertEqual(adapter.embed(['sample']), [[1.0, 0.0]])
        self.model.encode.assert_called_once_with(['sample'], batch_size=32,
            show_progress_bar=False, convert_to_numpy=True, normalize_embeddings=True, prompt='')
        with self.assertRaises(ValueError):
            adapter.embed('not a batch')

    def test_invalid_local_output_is_rejected(self):
        adapter = self.adapter()
        for output in ([[float('nan'), 0.0]], [[1.0]], [], [[True, 0.0]]):
            self.model.encode.return_value.tolist.return_value = output
            with self.subTest(output=output), self.assertRaises((ValueError, RuntimeError)):
                adapter.embed(['sample'])

    def test_local_dry_run_needs_neither_model_nor_optional_dependency(self):
        output = io.StringIO()
        with patch.object(run_eval, 'LocalSentenceTransformer') as factory:
            with contextlib.redirect_stdout(output):
                code = run_eval.main(['--embedder', 'local', '--dry-run', '--gate-suite', 'semantic'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['planned_embedding_requests'], 0)
        self.assertEqual(json.loads(output.getvalue())['planned_embedding_texts'], 54)
        factory.assert_not_called()

    def test_invalid_flags_fail_before_model_construction(self):
        with patch.object(run_eval, 'LocalSentenceTransformer') as factory:
            for args in (['--embedder', 'local'], ['--model-path', str(self.path)]):
                with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        run_eval.main(args)
                    self.assertEqual(caught.exception.code, 2)
            factory.assert_not_called()

    def test_local_report_provenance_without_exposing_model_path(self):
        # Fake vectors verify plumbing only; this report is never published as
        # a real-model measurement.
        adapter = FakeEmbedder()
        adapter.fingerprint = {'sha256': 'a' * 64, 'file_count': 2}
        adapter.runtime = {'sentence-transformers': 'mock'}
        adapter.settings = {'device': 'cpu'}
        output = io.StringIO()
        with patch.object(run_eval, 'LocalSentenceTransformer', return_value=adapter):
            with contextlib.redirect_stdout(output):
                code = run_eval.main(['--embedder', 'local', '--model-path', str(self.path),
                                      '--gate-suite', 'semantic', '--json'])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report['backend']['revision'], 'a' * 64)
        self.assertEqual(report['backend']['runtime_versions']['sentence-transformers'], 'mock')
        self.assertIsNone(report['backend']['endpoint_sha256'])
        self.assertNotIn(str(self.path), output.getvalue())


if __name__ == '__main__':
    unittest.main()
