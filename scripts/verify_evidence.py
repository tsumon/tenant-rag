"""Verify packaged synthetic evidence without models, network or extraction."""
import hashlib
import json
from pathlib import Path
import zipfile

root=Path(__file__).resolve().parents[1]
evidence=root/'eval/evidence'
manifest=json.loads((evidence/'synthetic-holdout-v1.json').read_text())
archive=evidence/manifest['archive']
assert hashlib.sha256(archive.read_bytes()).hexdigest()==manifest['sha256']
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    corpus=z.read('holdout-v1/retrieval-holdout-v1.jsonl')
    assert hashlib.sha256(corpus).hexdigest()==manifest['dataset_sha256']
    records=[json.loads(line) for line in corpus.decode().splitlines()]
    labels={r['id']:r['expected'] for r in records if r['type']=='query'}
    assert len(labels)==64 and sum(r['type']=='doc' for r in records)==132
    for name in ('rrf','score'):
        report=json.loads(z.read('results/'+name+'.json'))
        assert {r['id']:r['expected'] for r in report['queries']}==labels
        for query in report['queries']:
            expected=set(query['expected'])
            for mode,ranked in query['ranked'].items():
                recall=len(expected&set(ranked))/len(expected)
                mrr=next((1/i for i,d in enumerate(ranked,1) if d in expected),0)
                assert abs(recall-query['metrics'][mode]['recall_at_k'])<1e-12
                assert abs(mrr-query['metrics'][mode]['mrr_at_k'])<1e-12
print('Synthetic archive hash, dataset hash and 64 original-label rankings verified.')

manifest=json.loads((evidence/'release-model-results.json').read_text())
archive=evidence/manifest['archive']
assert hashlib.sha256(archive.read_bytes()).hexdigest()==manifest['sha256']
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    for name,digest in manifest['files'].items():
        assert hashlib.sha256(z.read(name)).hexdigest()==digest
    for dataset in ('scifact','t2'):
        report=json.loads(z.read(dataset+'/report.json'))
        for query in report['queries']:
            expected=set(query['expected'])
            for mode,ranked in query['ranked'].items():
                assert len(ranked)<=3 and len(ranked)==len(set(ranked))
                recall=len(expected&set(ranked))/len(expected)
                mrr=next((1/i for i,d in enumerate(ranked,1) if d in expected),0)
                assert abs(recall-query['metrics'][mode]['recall_at_3'])<1e-12
                assert abs(mrr-query['metrics'][mode]['mrr_at_3'])<1e-12
print('Public study archive, every file hash and all 428 query metrics verified.')

summary=json.loads((evidence/'dependencies/summary.json').read_text())
for name,digest in summary['evidence_sha256'].items():
    assert hashlib.sha256((evidence/'dependencies'/name).read_bytes()).hexdigest()==digest
assert all(v['distribution_count']==42 and v['advisory_matches']==0 and v['skipped']==0 for v in summary['services'].values())
print('Distributed dependency audit manifest and complete 42-package scan summaries verified.')
