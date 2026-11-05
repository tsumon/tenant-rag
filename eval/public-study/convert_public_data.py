#!/usr/bin/env python3
"""Deterministically convert pinned public datasets; no retrieval/model calls.

Install only pyarrow==26.0.1 (from PyPI) if needed. Sources and audit receipts
are in SOURCE_FILES.json. The selection was fixed before local rankings.
"""
from __future__ import annotations
import argparse, collections, csv, hashlib, json, re, sys
from pathlib import Path
import pyarrow.parquet as pq

SEED='public-retrieval-v1'
DIGIT=re.compile(r'[0-9]')
LONG_CHARS=2000
N_QUERIES=128
N_DISTRACTORS=2048


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf-8')


def write_jsonl(path, rows):
    with Path(path).open('w',encoding='utf-8',newline='\n') as f:
        for row in rows:
            f.write(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n')


def hashed_ids(ids, kind):
    return sorted(ids,key=lambda x:(hashlib.sha256(f'{SEED}:{kind}:{x}'.encode('utf-8')).digest(),x))


def read_parquet(path, id_key):
    rows=pq.read_table(path).to_pylist()
    result={str(x[id_key]):x for x in rows}
    assert len(result)==len(rows),f'duplicate IDs in {path}'
    assert all(isinstance(x[id_key],str) for x in rows),f'non-string IDs in {path}'
    assert all(isinstance(x['text'],str) and x['text'].strip() for x in rows),f'blank text in {path}'
    return result


def render_text(row):
    title=row.get('title','')
    # Preserve each original field byte-for-byte in UTF-8; only add one separator.
    return title+'\n'+row['text'] if title else row['text']


def quantiles(values):
    values=sorted(values)
    return {'min':values[0],'median':values[len(values)//2],'p90':values[int((len(values)-1)*.90)],'p99':values[int((len(values)-1)*.99)],'max':values[-1]}


def convert(root, name, language, corpus, queries, all_qrels, selected_qids, selected_docids, dataset_id, provenance, license_information, limitation):
    out=root/name;out.mkdir(exist_ok=True)
    selected_qids=set(selected_qids);selected_docids=set(selected_docids)
    assert selected_qids<=queries.keys() and selected_docids<=corpus.keys()
    qrels=[x for x in all_qrels if x['query-id'] in selected_qids]
    assert all(x['corpus-id'] in selected_docids for x in qrels)
    assert len({(x['query-id'],x['corpus-id']) for x in all_qrels})==len(all_qrels),'duplicate source qrels'
    by_query=collections.defaultdict(list)
    for row in qrels:
        assert type(row['score']) is int
        if row['score']>0: by_query[row['query-id']].append(row['corpus-id'])
    assert set(by_query)==selected_qids,'query lacks a published positive label'
    docs=[]
    for did in sorted(selected_docids):
        source=corpus[did];text=render_text(source)
        docs.append({'type':'doc','doc_id':did,'text':text,'metadata':{
            'source_dataset':dataset_id,'source_id':did,'language':language,
            'source_text_sha256':hashlib.sha256(source['text'].encode()).hexdigest(),
            'source_title':source.get('title',''),'chars':len(text)}})
    eval_queries=[]
    for qid in sorted(selected_qids):
        query=queries[qid]['text']; positive=sorted(by_query[qid]);tags=['public_benchmark',language]
        if DIGIT.search(query):tags.append('query_numeric')
        if any(len(render_text(corpus[x]))>=LONG_CHARS for x in positive):tags.append('relevant_doc_long')
        if any(DIGIT.search(render_text(corpus[x])) for x in positive):tags.append('relevant_doc_numeric')
        eval_queries.append({'type':'query','id':qid,'query':query,'expected':positive,'suite':'semantic','tags':tags,
            'source_dataset':dataset_id,'source_query_id':qid})
    qrels=sorted(qrels,key=lambda x:(x['query-id'],x['corpus-id']))
    write_jsonl(out/'golden.jsonl',docs+eval_queries)
    write_jsonl(out/'qrels.jsonl',qrels)
    write_json(out/'selected_query_ids.json',sorted(selected_qids))
    write_json(out/'selected_document_ids.json',sorted(selected_docids))
    counts={'source_corpus':len(corpus),'source_queries':len(queries),'source_qrels':len(all_qrels),
            'documents':len(docs),'queries':len(eval_queries),'qrels':len(qrels),
            'positive_qrels':sum(x['score']>0 for x in qrels),
            'grade_distribution':dict(sorted(collections.Counter(str(x['score']) for x in qrels).items())),
            'source_grade_distribution':dict(sorted(collections.Counter(str(x['score']) for x in all_qrels).items())),
            'tag_query_counts':dict(sorted(collections.Counter(t for x in eval_queries for t in x['tags']).items())),
            'corpus_document_chars':quantiles([len(x['text']) for x in docs]),
            'long_documents':sum(len(x['text'])>=LONG_CHARS for x in docs)}
    src_records=json.loads((root/'SOURCE_FILES.json').read_text())
    source_repos={'BeIR/scifact','BeIR/scifact-qrels'} if language=='en' else {'C-MTEB/T2Retrieval','C-MTEB/T2Retrieval-qrels'}
    manifest={
      'dataset_id':dataset_id,'version':SEED,'sha256':digest(out/'golden.jsonl'),
      'provenance':provenance,
      'label_policy':'Unchanged published source query/document IDs and qrel scores. expected contains exactly published score > 0 document IDs. No generated labels, manual overrides, or inferred negatives. Raw grades are preserved in qrels.jsonl; Recall/MRR use binary positive relevance, not official nDCG.',
      'selection_plan_sha256':digest(root/'SELECTION_PLAN.json'),
      'selection_plan':json.loads((root/'SELECTION_PLAN.json').read_text()),
      'counts':counts,'license_information':license_information,
      'limitations':limitation,
      'sources':[x for x in src_records if x['repository'] in source_repos],
      'files':{f:{'sha256':digest(out/f),'bytes':(out/f).stat().st_size} for f in ['golden.jsonl','qrels.jsonl','selected_query_ids.json','selected_document_ids.json']},
      'conversion':{'script_sha256':digest(__file__),'pyarrow_version':__import__('pyarrow').__version__,
                    'text':'Original source text, no cleaning, truncation, translation or synthetic expansion. A nonempty original title is prepended with one newline.',
                    'length_unit':'Python len(text), Unicode code points including source HTML markup; not model tokens.',
                    'tags':{'query_numeric':'[0-9] occurs in original query','relevant_doc_long':'some published positive document has >=2000 Unicode code points after title prepend','relevant_doc_numeric':'[0-9] occurs in some published positive document'}}}
    write_json(out/'manifest.json',manifest)
    return {'name':name,'counts':counts,'golden_sha256':manifest['sha256'],'manifest_sha256':digest(out/'manifest.json')}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).parent);args=parser.parse_args();root=args.root
    for record in json.loads((root/'SOURCE_FILES.json').read_text()):
        assert digest(root/record['local_path'])==record['sha256'],f"source hash mismatch: {record['local_path']}"
    enroot=root/'raw/BeIR'
    enc=read_parquet(enroot/'scifact/corpus/corpus-00000-of-00001.parquet','_id')
    enq=read_parquet(enroot/'scifact/queries/queries-00000-of-00001.parquet','_id')
    with (enroot/'scifact-qrels/test.tsv').open() as f:
        enrel=[{'query-id':x['query-id'],'corpus-id':x['corpus-id'],'score':int(x['score'])} for x in csv.DictReader(f,delimiter='\t')]
    enqids={x['query-id'] for x in enrel}
    assert len(enc)==5183 and len(enqids)==300
    summaries=[]
    summaries.append(convert(root,'scifact-test-full','en',enc,enq,enrel,enqids,enc.keys(),'BeIR/scifact:test',
        'BEIR SciFact published full corpus and test relevance split, pinned BeIR/scifact and BeIR/scifact-qrels Hugging Face revisions. SciFact upstream: Wadden et al., Fact or Fiction: Verifying Scientific Claims (2020), expert-written claims and evidence annotations. No benchmark labels authored for this evaluation.',
        {'upstream_claims_annotations':'CC-BY-4.0','upstream_abstract_database':'ODC-By-1.0','beir_hub_card':'CC-BY-SA-4.0; retained separately from upstream component licenses, not treated as replacing them',
         'upstream_license_url':'https://github.com/allenai/scifact/blob/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e/LICENSE.md',
         'hub_card_url':'https://huggingface.co/datasets/BeIR/scifact/blob/b3b5335604bf5ee3c4447671af975ea25143d4f5/README.md'},
        ['Full BEIR SciFact corpus/test split, but this evaluator reports Recall/MRR@3, not BEIR leaderboard nDCG@10.',
         'Scientific abstract retrieval, not a general-purpose or tenant-isolation benchmark.',
         'Unjudged documents are not proven irrelevant; labels can be incomplete.',
         'Embedding model training contamination is unknown. Source data remain outside the repository and distribution packages.',
         'No converter text truncation. The embedding model may truncate inputs; token/truncation evidence must be reported by the evaluator.']))
    zroot=root/'raw/C-MTEB'
    zc=read_parquet(zroot/'T2Retrieval/data/corpus-00000-of-00001-8afe7b7a7eca49e3.parquet','id')
    zq=read_parquet(zroot/'T2Retrieval/data/queries-00000-of-00001-930bf3b805a80dd9.parquet','id')
    zrel=[{'query-id':x['qid'],'corpus-id':x['pid'],'score':x['score']} for x in pq.read_table(zroot/'T2Retrieval-qrels/data/dev-00000-of-00001-92ed0416056ff7e1.parquet').to_pylist()]
    assert len(zc)==118605 and len(zq)==22812 and len(zrel)==118932
    assert {x['query-id'] for x in zrel}==set(zq)
    zqids=set(hashed_ids(zq.keys(),'query')[:N_QUERIES])
    judged={x['corpus-id'] for x in zrel if x['query-id'] in zqids}
    distractors=hashed_ids(zc.keys()-judged,'distractor')[:N_DISTRACTORS]
    zdids=judged|set(distractors)
    summaries.append(convert(root,'t2retrieval-dev-hash128','zh',zc,zq,zrel,zqids,zdids,'C-MTEB/T2Retrieval:dev:hash128',
        'Published C-MTEB T2Retrieval dev adaptation of THUIR T2Ranking. Preserve C-MTEB source IDs and labels, which need not be the full original T2Ranking corpus/ID space. THUIR documents expert relevance annotation; C-MTEB retrieval qrels are the published retrieval relevance representation, not reconstructed four-level reranking grades.',
        {'upstream':'Apache-2.0 stated by THUIR/T2Ranking authors','c_mteb_hub_card':'No license field in pinned C-MTEB cards; upstream author license plus mteb official repack card provide provenance',
         'upstream_license_url':'https://github.com/THUIR/T2Ranking/blob/3ab0a0de72dd50bf84d852a985f6188334781403/README.md#license',
         'mteb_card_url':'https://huggingface.co/datasets/mteb/T2Retrieval/blob/921dd3af6e78d1ae7ee0368aa8d7eaee02c8f08e/README.md',
         'license_text_url':'https://www.apache.org/licenses/LICENSE-2.0.html'},
        ['Outcome-blind 128-query, reduced-corpus slice with every source-qrel document for selected queries plus 2048 deterministic distractors. Not an official C-MTEB or full T2Ranking score.',
         'Reduced corpus omits many competing negatives and can materially inflate retrieval metrics; a full-corpus run is needed for a broader claim.',
         'C-MTEB adaptation is smaller than original THUIR T2Ranking. Its retrieval labels must not be described as original four-level reranking grades.',
         'Published source text can include HTML markup; preserved exactly rather than cleaned after outcome inspection.',
         'Unjudged distractors are not assumed true negatives; no labels were invented.',
         'Embedding model training contamination is unknown. No converter truncation; model token/truncation limits must be reported by evaluator.',
         'Source data remain outside repository and release packages.']))
    write_json(root/'SUMMARY.json',summaries)
    print(json.dumps(summaries,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
