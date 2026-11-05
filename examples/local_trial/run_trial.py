"""Offline, synthetic two-tenant trial against the installed v0.5.0 wheel.

This returns retrieved source excerpts, NOT generated answers.
"""
from __future__ import annotations
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import secrets
import sqlite3
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parent
CHUNK_OPTIONS = {'max_chars': 180, 'overlap': 30}
MODEL_REVISION = 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_output_paths(output):
    paths = (output, output.with_suffix('.timing.json'))
    require(paths[0] != paths[1], 'Report and timing paths must differ; use a .json report name')
    require(not any(p.exists() for p in paths),
            'Report or timing output exists; choose a new path to preserve evidence')


def load_data(directory):
    rows = [row.split('  ', 1) for row in (directory / 'SHA256SUMS').read_text().splitlines()]
    listed = {name for _, name in rows}
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    require(actual == listed | {'SHA256SUMS'}, 'Frozen fixture file set mismatch')
    for digest, name in rows:
        require(sha256(directory / name) == digest, f'Frozen fixture hash mismatch: {name}')
    documents = {}
    for tenant in ('aurora', 'cedar'):
        documents[tenant] = []
        for path in sorted((directory / tenant).glob('*.md')):
            documents[tenant].append({
                'doc_id': path.stem, 'text': path.read_text(encoding='utf-8'),
                'source': f'{tenant}/{path.name}', 'format': 'markdown',
                'metadata': {'tenant': tenant, 'synthetic': True},
            })
        require(bool(documents[tenant]), f'No source documents for {tenant}')
    queries = json.loads((directory / 'queries.json').read_text(encoding='utf-8'))
    for q in queries:
        require(q['tenant'] in documents, 'Unknown query tenant')
        sources = {d['doc_id']: d['text'] for d in documents[q['tenant']]}
        for r in q['relevant']:
            require(r['doc_id'] in sources and r['evidence'] in sources[r['doc_id']],
                    f"Invalid relevance evidence: {q['id']}")
    return documents, queries


def verify_installation_and_model(model_dir):
    """Verify bytes, not merely a version string or an advertised revision."""
    from scripts.download_assets import verify
    release = json.loads((ROOT / 'release-manifest.json').read_text())
    asset = next(a for a in release['assets'] if a['name'].endswith('.whl'))
    wheel = ROOT / 'wheel' / asset['name']
    require(wheel.is_file() and verify(wheel, asset), 'Official wheel is missing or fails its pinned checksum')
    dist = metadata.distribution('tenant-rag')
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.startswith(('tenantrag/', 'admin/')) and not name.endswith('/'):
                installed = Path(dist.locate_file(name))
                require(installed.is_file() and installed.read_bytes() == archive.read(name),
                        f'Installed package differs from verified release wheel: {name}')
    import tenantrag
    expected_package = Path(dist.locate_file('tenantrag')).resolve()
    for name, module in list(sys.modules.items()):
        if name == 'tenantrag' or name.startswith('tenantrag.'):
            imported = Path(module.__file__).resolve()
            require(imported.is_relative_to(expected_package),
                    'tenant-rag import is shadowed; remove PYTHONPATH/checkouts and use a clean venv')
    manifest = json.loads((ROOT / 'model-manifest.json').read_text())
    actual = {p.relative_to(model_dir).as_posix() for p in model_dir.rglob('*') if p.is_file()}
    require(actual == set(manifest['files']), 'Model directory must contain exactly the pinned snapshot files')
    for name, meta in manifest['files'].items():
        require(verify(model_dir / name, meta), f'Model checksum mismatch: {name}')
    return asset['digest'].removeprefix('sha256:')


def citation(hit, tenant, documents):
    meta = hit['metadata']
    require(meta.get('tenant') == tenant, 'Cross-tenant metadata detected')
    chunk = meta['_chunk']
    source = next((d for d in documents[tenant] if d['doc_id'] == chunk['parent_id']), None)
    require(source is not None and chunk['source'] == source['source'], 'Unexpected citation source')
    start, end = chunk['start'], chunk['end']
    require(type(start) is int and type(end) is int and 0 <= start < end <= len(source['text']),
            'Invalid citation offsets')
    require(source['text'][start:end] == hit['text'], 'Citation does not match exact source slice')
    return {'parent_id': chunk['parent_id'], 'source': chunk['source'],
            'start': start, 'end': end, 'text': hit['text'],
            'score': hit['score'], 'matched': hit['matched']}


def evaluate_query(query, hits):
    relevant = query['relevant']
    if not relevant:
        return {'out_of_scope_returned_chunks': len(hits)}
    ids = {r['doc_id'] for r in relevant}
    retrieved = {h['parent_id'] for h in hits}
    # Cutoff is raw top-3 CHUNKS. Duplicate parents retain their original ranks.
    first = next((rank for rank, h in enumerate(hits, 1) if h['parent_id'] in ids), None)
    covered = sum(any(h['parent_id'] == r['doc_id'] and r['evidence'] in h['text']
                      for h in hits) for r in relevant)
    return {'parent_recall_at_3_chunks': len(ids & retrieved) / len(ids),
            'parent_reciprocal_rank_at_3_chunks': 1 / first if first else 0,
            'exact_evidence_recall_at_3_chunks': covered / len(relevant)}


class CountingEmbedder:
    def __init__(self, model):
        self.model = model
        self.dim = model.dim
        self.embedding_identity = model.embedding_identity
        self.calls = 0
        self.texts = 0

    def embed(self, texts):
        self.calls += 1
        self.texts += len(texts)
        return self.model.embed(texts)


def run_scenario(embedder, documents, queries):
    from tenantrag import AuthError, TenantRAG
    wrapped = CountingEmbedder(embedder)
    # Keys live only in memory. The disposable database is not part of delivery.
    keys = {t: secrets.token_urlsafe(32) for t in documents}
    report = {'ingestion': {}, 'checks': {}, 'queries': [], 'summary': {}}
    with tempfile.TemporaryDirectory(prefix='tenant-rag-trial-') as temp:
        db = str(Path(temp) / 'trial.db')
        with TenantRAG(db, embedder=wrapped) as rag:
            for tenant, docs in documents.items():
                rag.register(tenant, keys[tenant], quota_docs=20)
                results = rag.ingest_batch(keys[tenant], docs, **CHUNK_OPTIONS)
                calls = wrapped.calls
                repeated = rag.ingest_batch(keys[tenant], docs, **CHUNK_OPTIONS)
                require(all(x['unchanged'] for x in repeated), 'Ingestion retry changed source')
                require(wrapped.calls == calls, 'Unchanged ingestion embedded again')
                report['ingestion'][tenant] = {'parents': len(results),
                    'chunks': sum(x['chunks'] for x in results),
                    'unchanged_retry_extra_embedding_calls': wrapped.calls - calls}
                listed = rag.list_docs(keys[tenant], limit=100)
                require(len(listed) == len(docs) and all(d['metadata']['tenant'] == tenant for d in listed),
                        'Tenant listing isolation failed')
                require(rag.usage(keys[tenant])['docs'] == len(docs), 'Parent quota count mismatch')

            calls = wrapped.calls
            try:
                rag.search(secrets.token_urlsafe(32), 'Invalid key must fail before embedding')
            except AuthError:
                pass
            else:
                raise RuntimeError('Invalid key accepted')
            require(calls == wrapped.calls, 'Invalid key caused embedding')
            report['checks']['invalid_key_rejected_before_embedding'] = True

            for query in queries:
                entry = {k: query[k] for k in ('id', 'tenant', 'query', 'category', 'relevant')}
                entry['modes'] = {}
                for mode in ('rrf', 'score'):
                    kwargs = {'fusion': mode}
                    if mode == 'score':
                        kwargs['lexical_weight'] = 0.2
                    hits = [citation(h, query['tenant'], documents) for h in
                            rag.search(keys[query['tenant']], query['query'], k=3, **kwargs)]
                    entry['modes'][mode] = {'hits': hits, **evaluate_query(query, hits)}
                report['queries'].append(entry)

            # Replay every query under the OTHER key and validate every returned
            # source slice belongs to that tenant, independent of answer quality.
            cross_key_checks = 0
            for query in queries:
                other = next(t for t in documents if t != query['tenant'])
                for mode in ('rrf', 'score'):
                    for hit in rag.search(keys[other], query['query'], k=100, fusion=mode):
                        citation(hit, other, documents)
                    cross_key_checks += 1
            report['checks']['other_key_query_runs'] = cross_key_checks
            report['checks']['other_key_results_all_in_correct_tenant'] = True

            same_ids = set(d['doc_id'] for d in documents['aurora']) & set(d['doc_id'] for d in documents['cedar'])
            require(bool(same_ids), 'Fixtures must reuse parent IDs across tenants')
            parent = sorted(same_ids)[0]
            before = rag.list_docs(keys['cedar'], limit=100)
            require(rag.delete(keys['aurora'], parent), 'Parent deletion failed')
            require(rag.list_docs(keys['cedar'], limit=100) == before, 'Delete affected another tenant')
            for query in queries:
                hits = rag.search(keys['aurora'], query['query'], k=100)
                require(all(h['metadata']['_chunk']['parent_id'] != parent for h in hits), 'Deleted chunks still visible')
            report['checks']['same_id_cross_tenant_delete_isolated'] = True
            report['checks']['deleted_parent_chunks_absent'] = True
        with TenantRAG(db, embedder=wrapped) as reopened:
            require(reopened.list_docs(keys['cedar'], limit=100) == before, 'Reopen changed tenant data')
            require(len(reopened.list_docs(keys['aurora'], limit=100)) == len(documents['aurora']) - 1,
                    'Deletion was not durable')
            report['checks']['reopen_preserves_data_and_deletion'] = True

    positive = [q for q in report['queries'] if q['relevant']]
    for mode in ('rrf', 'score'):
        report['summary'][mode] = {metric: sum(q['modes'][mode][metric] for q in positive) / len(positive)
            for metric in ('parent_recall_at_3_chunks', 'parent_reciprocal_rank_at_3_chunks',
                           'exact_evidence_recall_at_3_chunks')}
        report['summary'][mode]['answerable_queries'] = len(positive)
        report['summary'][mode]['out_of_scope_queries'] = sum(not q['relevant'] for q in queries)
        report['summary'][mode]['out_of_scope_queries_returning_any_hit'] = sum(
            bool(q['modes'][mode]['hits']) for q in report['queries'] if not q['relevant'])
    report['embedding_work'] = {'calls': wrapped.calls, 'texts': wrapped.texts}
    return report


def install_network_guard():
    attempts = []
    def audit(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname',
                     'socket.gethostbyaddr', 'socket.sendto'}:
            attempts.append(event)
            raise RuntimeError('Trial inference blocks Python socket network activity')
    sys.addaudithook(audit)
    return attempts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'trial-report.json')
    args = parser.parse_args()
    check_output_paths(args.output)
    os.environ.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                       'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false',
                       'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2'})
    require(metadata.version('tenant-rag') == '0.5.0', 'Install the official v0.5.0 wheel first')
    wheel_digest = verify_installation_and_model(args.model_dir)
    documents, queries = load_data(ROOT / 'data')
    attempted = install_network_guard()
    started = time.perf_counter()
    from local_embedder import LocalSentenceTransformer
    import tenantrag
    from tenantrag.chunking import chunk_text
    model = LocalSentenceTransformer(args.model_dir)
    model_loaded = time.perf_counter()
    token_counts = []
    for docs in documents.values():
        for doc in docs:
            for chunk in chunk_text(doc['text'], format='markdown', **CHUNK_OPTIONS):
                count = len(model.model.tokenizer.encode(chunk['text'], add_special_tokens=True, truncation=False))
                token_counts.append(count)
    query_counts = [len(model.model.tokenizer.encode(q['query'], add_special_tokens=True, truncation=False)) for q in queries]
    require(max(token_counts + query_counts) <= model.model.max_seq_length,
            'A chunk/query exceeds this model token limit; do not silently truncate')
    report = run_scenario(model, documents, queries)
    report['provenance'] = {'tenant_rag_version': metadata.version('tenant-rag'),
        'source_commit': json.loads((ROOT / 'release-manifest.json').read_text())['source_commit'],
        'wheel_sha256': wheel_digest,
        'model_repository': 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
        'model_upstream_revision': MODEL_REVISION, 'model_fingerprint': model.fingerprint,
        'runtime': model.runtime, 'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
        'model_settings': model.settings, 'fixture_sha256': {
            p.relative_to(ROOT / 'data').as_posix(): sha256(p)
            for p in sorted((ROOT / 'data').rglob('*')) if p.is_file()},
        'implementation_sha256': {n: sha256(ROOT / n) for n in ('run_trial.py', 'local_embedder.py')}}
    report['token_audit'] = {'chunk_count': len(token_counts), 'max_chunk_tokens': max(token_counts),
        'max_query_tokens': max(query_counts), 'model_limit': model.model.max_seq_length,
        'truncated_chunks': 0, 'chunk_options': CHUNK_OPTIONS}
    report['network'] = {'guard': 'Python socket audit hook + offline model-loading flags',
                         'attempted_network_events': attempted,
                         'scope': 'Inference process only; setup downloads are separate. Not an OS firewall.'}
    require(not attempted, 'Unexpected network attempts during offline inference')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    timings = {'load_seconds': model_loaded - started, 'scenario_seconds': time.perf_counter() - model_loaded,
               'verified_installed_package': True, 'not_a_benchmark': True}
    with args.output.with_suffix('.timing.json').open('x', encoding='utf-8') as output:
        output.write(json.dumps(timings, indent=2) + '\n')
    print(json.dumps({'summary': report['summary'], 'checks': report['checks'],
                      'token_audit': report['token_audit'], 'network': report['network']}, ensure_ascii=False, indent=2))
    print(f'Full source excerpts and provenance: {args.output}')


if __name__ == '__main__':
    main()
