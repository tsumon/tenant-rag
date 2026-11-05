"""Recompute public study metrics, top-k ceilings and paired changes from IDs."""
import argparse
import json
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('directory',type=Path);a=p.parse_args()
report=json.loads((a.directory/'report.json').read_text())
rows=report['queries']
for row in rows:
    for mode,ranking in row['ranked'].items():
        assert len(ranking)<=3 and len(ranking)==len(set(ranking))
        recall=len(set(ranking)&set(row['expected']))/len(set(row['expected']))
        mrr=next((1/i for i,key in enumerate(ranking,1) if key in row['expected']),0)
        assert abs(recall-row['metrics'][mode]['recall_at_3'])<1e-12
        assert abs(mrr-row['metrics'][mode]['mrr_at_3'])<1e-12
changes=[]
for row in rows:
    deltas={baseline:{metric:row['metrics']['score'][metric]-row['metrics'][baseline][metric]
        for metric in ('recall_at_3','mrr_at_3')} for baseline in ('rrf','vector')}
    if any(value for metrics in deltas.values() for value in metrics.values()):
        changes.append({'id':row['id'],'tags':row['tags'],'expected':row['expected'],
                        'ranked':row['ranked'],'score_minus_baseline':deltas})
summary={'query_count':len(rows),'positive_count_min':min(len(q['expected']) for q in rows),
 'positive_count_max':max(len(q['expected']) for q in rows),
 'queries_with_more_than_k_positives':sum(len(q['expected'])>3 for q in rows),
 'macro_recall_at_3_ceiling':sum(min(3,len(q['expected']))/len(q['expected']) for q in rows)/len(rows),
 'paired_counts':{base:{metric:{'improved':sum(q['metrics']['score'][metric]>q['metrics'][base][metric] for q in rows),
     'worsened':sum(q['metrics']['score'][metric]<q['metrics'][base][metric] for q in rows),
     'unchanged':sum(q['metrics']['score'][metric]==q['metrics'][base][metric] for q in rows)}
     for metric in ('recall_at_3','mrr_at_3')} for base in ('rrf','vector')},
 'changes':changes,'note':'All score-candidate losses and gains retained; no significance claim or parameter retuning.'}
(a.directory/'analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k!='changes'},indent=2))
