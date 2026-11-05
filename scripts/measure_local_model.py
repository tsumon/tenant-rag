"""Optional warm single-query CPU inference measurement; no quality tuning."""
import json,os
from pathlib import Path
import socket,statistics,sys,time
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
def deny(*a,**k):raise RuntimeError('network disabled')
socket.create_connection=socket.getaddrinfo=socket.socket.connect=socket.socket.connect_ex=deny
from eval.local_embedder import LocalSentenceTransformer
model_path,data_root,output=sys.argv[1:]
model=LocalSentenceTransformer(model_path)
rows=[]
for name in ('scifact-test-full','t2retrieval-dev-hash128'):
    qs=[json.loads(l) for l in (Path(data_root)/name/'golden.jsonl').read_text().splitlines() if json.loads(l)['type']=='query']
    rows.extend((name,q) for q in sorted(qs,key=lambda q:q['id'])[:8])
for _,q in rows[:3]:model.embed([q['query']])
times=[]
for name,q in rows:
    start=time.perf_counter();model.embed([q['query']]);elapsed=time.perf_counter()-start
    times.append({'dataset':name,'query_id':q['id'],'seconds':elapsed})
values=sorted(r['seconds'] for r in times)
result={'rows':times,'warmup_calls':3,'p50_seconds':statistics.median(values),'p95_order_statistic_seconds':values[int((len(values)-1)*.95)],
 'model_files':model.fingerprint,'runtime':model.runtime,'cpu_threads':os.environ.get('OMP_NUM_THREADS'),
 'note':'16 fixed-ID warm single-query calls, one observation each. Does not include indexing/search, network or model load. Not a dedicated-host service SLA.'}
Path(output).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
