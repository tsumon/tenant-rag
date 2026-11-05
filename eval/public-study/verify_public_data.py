#!/usr/bin/env python3
"""Independent source/label/selection checks. Does not load an embedding model."""
from pathlib import Path
import argparse,collections,csv,hashlib,json,re,sys
parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
root=parser.parse_args().root
import pyarrow.parquet as pq

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def readlines(p):return [json.loads(l) for l in p.read_text().splitlines()]
def rank_id(i,kind):return hashlib.sha256(('public-retrieval-v1:'+kind+':'+i).encode()).hexdigest(),i
for source in json.loads((root/'SOURCE_FILES.json').read_text()):assert sha(root/source['local_path'])==source['sha256']
results=[]
for name,corpus_path,queries_path,idkey,qrels_path,language in [
 ('scifact-test-full','raw/BeIR/scifact/corpus/corpus-00000-of-00001.parquet','raw/BeIR/scifact/queries/queries-00000-of-00001.parquet','_id','raw/BeIR/scifact-qrels/test.tsv','en'),
 ('t2retrieval-dev-hash128','raw/C-MTEB/T2Retrieval/data/corpus-00000-of-00001-8afe7b7a7eca49e3.parquet','raw/C-MTEB/T2Retrieval/data/queries-00000-of-00001-930bf3b805a80dd9.parquet','id','raw/C-MTEB/T2Retrieval-qrels/data/dev-00000-of-00001-92ed0416056ff7e1.parquet','zh')]:
 corpus={x[idkey]:x for x in pq.read_table(root/corpus_path).to_pylist()}; queries={x[idkey]:x for x in pq.read_table(root/queries_path).to_pylist()}
 if language=='en':
  with (root/qrels_path).open() as f:rels=[(x['query-id'],x['corpus-id'],int(x['score'])) for x in csv.DictReader(f,delimiter='\t')]
 else:rels=[(x['qid'],x['pid'],x['score']) for x in pq.read_table(root/qrels_path).to_pylist()]
 assert all(q in queries and d in corpus for q,d,s in rels)
 assert len(rels)==len(set((q,d) for q,d,s in rels))
 chosenq={q for q,d,s in rels} if language=='en' else set(sorted(queries,key=lambda q:rank_id(q,'query'))[:128])
 mandatory={d for q,d,s in rels if q in chosenq}
 chosend=set(corpus) if language=='en' else mandatory|set(sorted(set(corpus)-mandatory,key=lambda d:rank_id(d,'distractor'))[:2048])
 folder=root/name; manifest=json.loads((folder/'manifest.json').read_text())
 for file,metadata in manifest['files'].items():assert sha(folder/file)==metadata['sha256']
 data=readlines(folder/'golden.jsonl');docs={x['doc_id']:x for x in data if x['type']=='doc'};qs={x['id']:x for x in data if x['type']=='query'}
 assert set(docs)==chosend and set(qs)==chosenq
 assert set(json.loads((folder/'selected_document_ids.json').read_text()))==chosend
 assert set(json.loads((folder/'selected_query_ids.json').read_text()))==chosenq
 qrels=readlines(folder/'qrels.jsonl')
 assert {(x['query-id'],x['corpus-id'],x['score']) for x in qrels}=={(q,d,s) for q,d,s in rels if q in chosenq}
 positives=collections.defaultdict(set)
 for q,d,s in rels:
  if s>0:positives[q].add(d)
 for did,row in docs.items():
  source=corpus[did]; original=(source['title']+'\n' if source.get('title') else '')+source['text']
  assert row['text']==original and row['metadata']['source_id']==did
 for qid,row in qs.items():
  assert row['query']==queries[qid]['text'] and set(row['expected'])==positives[qid]
  assert ('query_numeric' in row['tags'])==bool(re.search('[0-9]',row['query']))
  assert ('relevant_doc_long' in row['tags'])==any(len(docs[d]['text'])>=2000 for d in positives[qid])
  assert ('relevant_doc_numeric' in row['tags'])==any(re.search('[0-9]',docs[d]['text']) for d in positives[qid])
 results.append({'dataset':name,'documents':len(docs),'queries':len(qs),'qrels':len(qrels),'result':'PASS'})
print(json.dumps({'source_integrity':'PASS','source_label_identity':'PASS','predeclared_selection':'PASS','unmodified_text':'PASS','outcome_blind_tags':'PASS','datasets':results},indent=2))
