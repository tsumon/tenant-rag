"""Fixed public-data retrieval diagnostics with a trusted local model only.

No training, tuning, downloads, or hosted calls. Query/document embeddings are
computed in batches and cached in memory. Search timings exclude model calls;
these are single-process observations, not production latency guarantees.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import socket
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
from eval.local_embedder import LocalSentenceTransformer
from eval.run_eval import load_golden, dataset_manifest, implementation_fingerprint, recall_at_k, mrr
from tenantrag import TenantRAG
from tenantrag.search import build_fts_query


def aggregate(rows):
    return {mode:{metric:sum(r['metrics'][mode][metric] for r in rows)/len(rows)
                  for metric in ('recall_at_3','mrr_at_3')} for mode in ('rrf','score','vector','fts')}


def percentile(values, fraction):
    ordered=sorted(values)
    return ordered[min(len(ordered)-1,int((len(ordered)-1)*fraction))]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--golden',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--model-path',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=False)
    docs,queries=load_golden(args.golden)
    manifest=dataset_manifest(args.golden,docs,queries,args.manifest)
    attempts=[]
    def deny(*a,**k):
        attempts.append(True)
        raise RuntimeError('outbound networking disabled during evaluation')
    socket.create_connection=socket.getaddrinfo=socket.socket.connect=socket.socket.connect_ex=deny
    started=time.perf_counter()
    model=LocalSentenceTransformer(args.model_path)
    model_load=time.perf_counter()-started
    lengths=[]
    for kind,records in [('doc',docs),('query',queries)]:
        for row in records:
            text=row['text'] if kind=='doc' else row['query']
            lengths.append({'type':kind,'id':row['doc_id'] if kind=='doc' else row['id'],
                'tokens':len(model.model.tokenizer(text,add_special_tokens=True,truncation=False)['input_ids'])})
    cache={}; embedding_times={}
    for kind,texts in [('documents',[d['text'] for d in docs]),('queries',[q['query'] for q in queries])]:
        pending=list(dict.fromkeys(t for t in texts if t not in cache))
        start=time.perf_counter()
        for offset in range(0,len(pending),32):
            batch=pending[offset:offset+32]
            cache.update(zip(batch,model.embed(batch)))
            if offset%512==0: print(kind,offset,'/',len(pending),flush=True)
        embedding_times[kind]={'unique_new_texts':len(pending),'seconds':time.perf_counter()-start}
    class CachedModel:
        dim=model.dim
        embedding_identity=model.embedding_identity
        def embed(self,texts):return [cache[t] for t in texts]
    rows=[];latencies={mode:[] for mode in ('rrf','score','vector')}
    with tempfile.TemporaryDirectory() as tmp:
        with TenantRAG(str(Path(tmp)/'evaluation.db'),embedder=CachedModel()) as rag:
            rag.register('public-eval','public-eval-test-key',quota_docs=max(10000,len(docs)))
            start=time.perf_counter()
            for doc in docs:
                rag.index('public-eval-test-key',doc['text'],doc_id=doc['doc_id'],metadata=doc.get('metadata'))
            indexing_seconds=time.perf_counter()-start
            for number,query in enumerate(queries):
                rankings={}
                for mode,settings in [('rrf',{'fusion':'rrf'}),('score',{'fusion':'score','lexical_weight':0.2}),('vector',{'fusion':'score','lexical_weight':0})]:
                    start=time.perf_counter()
                    hits=rag.search('public-eval-test-key',query['query'],k=3,**settings)
                    latencies[mode].append(time.perf_counter()-start)
                    rankings[mode]=[(h['doc_id'],h['score']) for h in hits]
                expression=build_fts_query(query['query'])
                fts=rag.store.fts_search('public-eval',expression,50) if expression else []
                rankings['fts']=fts[:3]
                rows.append({'id':query['id'],'expected':query['expected'],'tags':query.get('tags',[]),
                    'ranked':{mode:[key for key,_ in ranked] for mode,ranked in rankings.items()},
                    'metrics':{mode:{'recall_at_3':recall_at_k(query['expected'],ranked,3),'mrr_at_3':mrr(query['expected'],ranked)} for mode,ranked in rankings.items()}})
                if number%25==0: print('queries',number,'/',len(queries),flush=True)
            database_bytes=sum(p.stat().st_size for p in Path(tmp).iterdir() if p.is_file())
    groups={'all':rows}
    for tag in sorted({tag for row in rows for tag in row['tags']}):groups[tag]=[r for r in rows if tag in r['tags']]
    report={'schema_version':'public-retrieval-study-v1','dataset':manifest,'k':3,
        'settings':{'lexical_weight':0.2,'rrf_k':60,'candidates_per_channel':50,'chunking':False,'positive_cosine_only':True,'tuning_on_this_data':False},
        'backend':{'model_files':model.fingerprint,'runtime_versions':model.runtime,'settings':model.settings},
        'implementation_sha256':implementation_fingerprint(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'python_version':platform.python_version(),'sqlite_version':sqlite3.sqlite_version,
        'slices':{name:{'queries':len(selected),'metrics':aggregate(selected)} for name,selected in groups.items()},'queries':rows,
        'token_audit':{'model_limit':model.model.max_seq_length,'max_tokens':max(r['tokens'] for r in lengths),
            'truncated_documents':sum(r['type']=='doc' and r['tokens']>model.model.max_seq_length for r in lengths),
            'truncated_queries':sum(r['type']=='query' and r['tokens']>model.model.max_seq_length for r in lengths)},
        'blocked_network_attempts':len(attempts),
        'limitations':['Original public labels can be incomplete; unjudged documents are not newly annotated negatives.',
            'Numeric-query tags indicate digits, not verified numerical-reasoning questions.',
            'No claim that model training excluded these public sources.',
            'Whole-document index uses model truncation; not a chunked or official leaderboard run.',
            'The Chinese corpus is a deterministic reduced-corpus diagnostic.']}
    cost={'model_load_seconds':model_load,'embedding_batches':embedding_times,'indexing_cached_embeddings_seconds':indexing_seconds,
        'search_without_embedding_seconds':{mode:{'mean':statistics.mean(values),'p50':statistics.median(values),'p95_order_statistic':percentile(values,.95)} for mode,values in latencies.items()},
        'database_bytes_including_wal':database_bytes,'peak_process_rss_kib_linux':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'cpu_threads':os.environ.get('OMP_NUM_THREADS'),'platform':platform.platform(),'hosted_api_requests':0,'hosted_api_cost':0,
        'limitations':'One process on this cloud CPU; batch embedding cache changes cost structure. Search timing excludes model calls and is not production end-to-end latency. No energy or infrastructure price estimate.'}
    for name,value in [('report.json',report),('cost.json',cost),('token-lengths.json',lengths)]:
        (args.output_dir/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+'\n')
    print(json.dumps({'metrics':report['slices']['all'],'token_audit':report['token_audit'],'cost':cost},indent=2),flush=True)

if __name__=='__main__':main()
