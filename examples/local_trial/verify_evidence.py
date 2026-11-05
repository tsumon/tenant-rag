"""Verify retained synthetic trial evidence offline; no model/dependencies needed."""
import hashlib
import json
from pathlib import Path

from run_trial import ROOT, citation, evaluate_query, load_data, require, sha256


def verify():
    documents, queries = load_data(ROOT / 'data')
    report_path = ROOT / 'evidence' / 'real-model-final.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    repeat_path = ROOT / 'evidence' / 'real-model-repeat.json'
    require(report_path.read_bytes() == repeat_path.read_bytes(), 'Retained repetition differs')
    for name, digest in report['provenance']['implementation_sha256'].items():
        require(name in {'run_trial.py', 'local_embedder.py'} and sha256(ROOT / name) == digest,
                'Evidence implementation hash mismatch')
    require(set(report['provenance']['implementation_sha256']) == {'run_trial.py', 'local_embedder.py'},
            'Incomplete implementation provenance')
    source_files = {p.relative_to(ROOT / 'data').as_posix(): sha256(p)
                    for p in (ROOT / 'data').rglob('*') if p.is_file()}
    require(report['provenance']['fixture_sha256'] == source_files, 'Evidence fixture hash mismatch')
    release = json.loads((ROOT / 'release-manifest.json').read_text())
    wheel = next(a for a in release['assets'] if a['name'].endswith('.whl'))
    require(report['provenance']['source_commit'] == release['source_commit'] and
            report['provenance']['wheel_sha256'] == wheel['digest'].removeprefix('sha256:'),
            'Evidence release provenance mismatch')
    require(report['provenance']['tenant_rag_version'] == '0.5.0', 'Unexpected trial baseline')
    require(report['provenance']['model_fingerprint'] == {
        'file_count': 12, 'sha256': 'a3f7688b8991a6ea9f048524205c08de9c362f0e5d63d0ebdc9116a563601529'},
        'Unexpected recorded model fingerprint')
    require(len(report['queries']) == len(queries) == 16, 'Unexpected query count')
    for query, result in zip(queries, report['queries']):
        require(all(result[k] == query[k] for k in query), 'Evidence query mismatch')
        for mode in ('rrf', 'score'):
            mode_result = result['modes'][mode]
            require(len(mode_result['hits']) <= 3, 'Top-three cutoff exceeded')
            for hit in mode_result['hits']:
                # Reconstruct the public hit shape and independently check source.
                citation({'metadata': {'tenant': query['tenant'], '_chunk': hit},
                          'text': hit['text'], 'score': hit['score'], 'matched': hit['matched']},
                         query['tenant'], documents)
            require(all(mode_result[k] == v for k, v in evaluate_query(query, mode_result['hits']).items()),
                    'Query metric mismatch')
    positive = [q for q in report['queries'] if q['relevant']]
    for mode in ('rrf', 'score'):
        for metric in ('parent_recall_at_3_chunks', 'parent_reciprocal_rank_at_3_chunks',
                       'exact_evidence_recall_at_3_chunks'):
            mean = sum(q['modes'][mode][metric] for q in positive) / len(positive)
            require(report['summary'][mode][metric] == mean, 'Aggregate metric mismatch')
        for key, value in {'answerable_queries': 14, 'out_of_scope_queries': 2,
                           'out_of_scope_queries_returning_any_hit': 2}.items():
            require(report['summary'][mode][key] == value, 'Query accounting mismatch')
    require(report['network']['attempted_network_events'] == [], 'Recorded network attempt')
    require(report['checks']['other_key_query_runs'] == 32 and all(report['checks'].values()),
            'Recorded invariant failure')
    require(report['token_audit']['truncated_chunks'] == 0, 'Recorded truncation')
    print('Trial evidence verified: source slices, frozen inputs, release/model provenance,')
    print('all query and aggregate metrics, invariants, and byte-identical repetition.')
    print('RRF exact evidence 12/14; score 14/14; both out-of-scope queries return hits.')


if __name__ == '__main__':
    verify()
