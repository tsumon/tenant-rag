"""CPU-only evaluation adapter for trusted local Sentence Transformers models.

No model IDs, downloads, remote code, or paid providers are accepted. The core
package does not depend on sentence-transformers or torch.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path

from tenantrag.embed import Embedder, _validate_texts
from tenantrag.identity import EmbeddingIdentity


class LocalModelError(ValueError):
    """Actionable error without model contents or private paths."""


def _model_layout(root):
    modules = json.loads((root / 'modules.json').read_text(encoding='utf-8'))
    expected = ['sentence_transformers.models.Transformer', 'sentence_transformers.models.Pooling']
    if (not isinstance(modules, list) or len(modules) not in (2, 3)
            or any(not isinstance(module, dict) for module in modules)
            or [module.get('type') for module in modules[:2]] != expected
            or (len(modules) == 3 and modules[2].get('type') != 'sentence_transformers.models.Normalize')
            or modules[0].get('path') != ''):
        raise ValueError('unsupported model layout')
    for index, module in enumerate(modules):
        if (not module.keys() <= {'idx', 'name', 'path', 'type'}
                or module.get('idx') != index or module.get('name') != str(index)):
            raise ValueError('unsupported module configuration')
        relative = module.get('path')
        if (not isinstance(relative, str) or Path(relative).is_absolute()
                or any(part in {'..', '.git'} for part in Path(relative).parts)):
            raise ValueError('module path must stay inside the snapshot')
    if not modules[1]['path']:
        raise ValueError('Pooling requires its own local directory')
    sentence_config = root / 'config_sentence_transformers.json'
    if sentence_config.exists():
        settings = json.loads(sentence_config.read_text(encoding='utf-8'))
        if (not isinstance(settings, dict) or settings.get('prompts')
                or settings.get('default_prompt_name') is not None):
            raise ValueError('prompted models require a separate adapter')
    tokenizer_config = root / 'tokenizer_config.json'
    if tokenizer_config.exists():
        settings = json.loads(tokenizer_config.read_text(encoding='utf-8'))
        if (not isinstance(settings, dict) or any(value and key.endswith(('_file', '_files'))
                for key, value in settings.items())):
            raise ValueError('explicit tokenizer file overrides are unsupported; use self-contained standard tokenizer files')
    configs = list(root.glob('sentence_*_config.json'))
    if len(configs) > 1:
        raise ValueError('ambiguous transformer configuration')
    transformer_config = json.loads(configs[0].read_text(encoding='utf-8')) if configs else {}
    if (not isinstance(transformer_config, dict)
            or not transformer_config.keys() <= {'max_seq_length', 'do_lower_case'}):
        raise ValueError('unsupported transformer configuration')
    if 'max_seq_length' in transformer_config and (
            type(transformer_config['max_seq_length']) is not int or transformer_config['max_seq_length'] < 1):
        raise ValueError('invalid maximum sequence length')
    if 'do_lower_case' in transformer_config and type(transformer_config['do_lower_case']) is not bool:
        raise ValueError('invalid lower-case setting')
    pooling_config = json.loads((root / modules[1]['path'] / 'config.json').read_text(encoding='utf-8'))
    if not isinstance(pooling_config, dict):
        raise ValueError('invalid pooling configuration')
    return modules, transformer_config, pooling_config


def model_fingerprint(model_path):
    """Hash relative names and bytes, never machine-specific absolute paths.

    Snapshot file symlinks are followed. Trust the directory and keep it
    unchanged while loading/running the model.
    """
    root = Path(model_path).expanduser()
    if not root.is_dir() or not (root / "modules.json").is_file():
        raise LocalModelError("--model-path must be an existing Sentence Transformers directory containing modules.json; model IDs are not downloaded")
    try:
        paths = list(root.rglob("*"))
        if any(path.is_symlink() and path.is_dir() for path in paths):
            raise LocalModelError("model snapshot must not contain directory symlinks; file symlinks are supported")
        files = sorted(path for path in paths if path.is_file()
                       and ".git" not in path.relative_to(root).parts)
        _model_layout(root)
        if any(path.name == 'adapter_config.json' for path in files):
            raise LocalModelError('adapter/PEFT snapshots are unsupported; use a complete standalone model')
        for index in (path for path in files if path.name.endswith('.safetensors.index.json')):
            weight_map = json.loads(index.read_text(encoding='utf-8'))['weight_map']
            if not isinstance(weight_map, dict) or not weight_map:
                raise ValueError('invalid shard index')
            for shard in weight_map.values():
                if (not isinstance(shard, str) or Path(shard).is_absolute()
                        or any(part in {'..', '.git'} for part in Path(shard).parts)
                        or not shard.endswith('.safetensors') or not (index.parent / shard).is_file()):
                    raise ValueError('shard must reference a local safetensors file')
        if not any(path.suffix == ".safetensors" for path in files):
            raise LocalModelError("local evaluation requires safetensors weights; legacy pickle-based weights are not supported")
        if any(path.suffix.lower() in {".bin", ".pt", ".pth", ".pkl", ".pickle", ".py"}
               for path in files):
            raise LocalModelError("use a trusted safetensors-only snapshot without Python or pickle-based model files")
        hashes = {}
        for path in files:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            hashes[path.relative_to(root).as_posix()] = digest.hexdigest()
        digest = hashlib.sha256(json.dumps(hashes, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
    except LocalModelError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise LocalModelError("unable to read a supported local model snapshot; use standard Transformer/Pooling/Normalize modules") from None
    return {"sha256": digest, "file_count": len(hashes)}


class LocalSentenceTransformer(Embedder):
    def __init__(self, model_path):
        self.fingerprint = model_fingerprint(model_path)
        try:
            from sentence_transformers import SentenceTransformer, models
        except ImportError:
            raise LocalModelError("local model dependencies are unavailable; install the optional local-eval extra with python -m pip install '.[local-eval]' in a network-enabled environment") from None
        except Exception:
            raise LocalModelError("local model dependency import failed; check the optional dependency versions in a clean environment") from None
        try:
            root = Path(model_path).expanduser().resolve()
            layout, transformer_config, pooling_config = _model_layout(root)
            # Construct only known modules. SentenceTransformer(path) has
            # version-specific .load fallbacks that can drop offline kwargs.
            offline = {'local_files_only': True, 'trust_remote_code': False}
            transformer = models.Transformer(str(root),
                model_args={**offline, 'use_safetensors': True},
                tokenizer_args=dict(offline), config_args=dict(offline),
                **transformer_config)
            layers = [transformer, models.Pooling(**pooling_config)]
            if len(layout) == 3:
                layers.append(models.Normalize())
            self.model = SentenceTransformer(modules=layers, device='cpu')
            self.dim = self.model.get_sentence_embedding_dimension()
            if type(self.dim) is not int or self.dim < 1:
                raise ValueError("invalid model dimension")
            if self.fingerprint != model_fingerprint(model_path):
                raise ValueError("model files changed during load")
        except Exception:
            raise LocalModelError("local model load failed; check optional dependency compatibility and a complete, unchanged safetensors snapshot; no model download fallback is enabled") from None
        self.embedding_identity = {"provider": "sentence-transformers-local",
                                   "model": "local-snapshot",
                                   "revision": self.fingerprint["sha256"]}
        self.runtime = {}
        for package in ("sentence-transformers", "transformers", "torch", "tokenizers", "safetensors", "numpy"):
            try:
                self.runtime[package] = metadata.version(package)
            except metadata.PackageNotFoundError:
                self.runtime[package] = "unavailable"
        self.settings = {"device": "cpu", "normalize_embeddings": True,
                         "batch_size": 32, "prompt": "",
                         "max_seq_length": self.model.max_seq_length,
                         "local_files_only": True, "trust_remote_code": False,
                         "weights_format": "safetensors"}

    def embed(self, texts):
        _validate_texts(texts)
        if not texts:
            return []
        # Explicit empty prompt avoids hidden default_prompt_name behavior.
        # Models requiring query/document prefixes need a separate adapter.
        vectors = self.model.encode(texts, batch_size=32, show_progress_bar=False,
                                    convert_to_numpy=True, normalize_embeddings=True,
                                    prompt="").tolist()
        dimension = EmbeddingIdentity.validate(vectors, len(texts))
        if dimension != self.dim:
            raise LocalModelError("local model output dimension changed")
        return vectors
