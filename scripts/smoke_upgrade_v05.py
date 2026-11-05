"""Run -I with installed 0.4/0.5 interpreters against a disposable test database.

Never use a user/production database. Steps: seed (0.4), upgrade (0.5), rollback
(0.4), with one common fresh directory. No external embedding calls are made.
"""
import json
from importlib.metadata import version
from pathlib import Path
import sqlite3
import sys
from tenantrag import TenantRAG

phase, directory = sys.argv[1:]
root = Path(directory)
root.mkdir(parents=True, exist_ok=True)
db, baseline = root/'fixture.db', root/'baseline.json'
key_a, key_b = 'upgrade-smoke-tenant-a', 'upgrade-smoke-tenant-b'
def schema():
    with sqlite3.connect(db) as con:
        return con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()

def state(rag):
    return {'ranked':rag.search(key_a,'alpha launch',k=5),
            'usage':rag.usage(key_a),'schema':schema()}

if phase == 'seed':
    assert version('tenant-rag') == '0.4.0'
    if db.exists():
        raise SystemExit('Refusing to overwrite an existing fixture database')
    with TenantRAG(str(db)) as rag:
        rag.register('a',key_a);rag.register('b',key_b)
        rag.index(key_a,'alpha launch schedule',doc_id='one',metadata={'purpose':'upgrade fixture'})
        rag.ingest(key_a,'alpha details. '*30,doc_id='long',source='fixture.txt',max_chars=80,overlap=10)
        rag.index(key_b,'another tenant secret fixture',doc_id='one')
        baseline.write_text(json.dumps(state(rag),sort_keys=True))
    with sqlite3.connect(db) as source, sqlite3.connect(root/'backup.db') as target:
        source.backup(target)
        assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
elif phase == 'upgrade':
    assert version('tenant-rag') == '0.5.0'
    expected=json.loads(baseline.read_text())
    with TenantRAG(str(db)) as rag:
        assert json.loads(json.dumps(state(rag)))==expected
        assert rag.search(key_a,'alpha launch')==rag.search(key_a,'alpha launch',fusion='rrf')
        for weight in (0,0.2,1):
            assert all('another tenant' not in hit['text'] for hit in rag.search(key_a,'alpha',fusion='score',lexical_weight=weight))
        rag.index(key_a,'alpha compatibility update',doc_id='new')
    with TenantRAG(str(root/'backup.db')) as restored:
        assert restored.usage(key_a)==expected['usage']
        assert restored.search(key_a,'alpha launch',k=5)==expected['ranked']
    assert json.loads(json.dumps(schema()))==expected['schema']
elif phase == 'rollback':
    assert version('tenant-rag') == '0.4.0'
    expected=json.loads(baseline.read_text())
    with TenantRAG(str(db)) as rag:
        assert rag.usage(key_a)['docs']==expected['usage']['docs']+1
        assert any(h['doc_id']=='new' for h in rag.search(key_a,'compatibility',k=10))
        assert all('another tenant' not in h['text'] for h in rag.search(key_a,'alpha',k=10))
    assert json.loads(json.dumps(schema()))==expected['schema']
else:
    raise SystemExit('Expected seed, upgrade or rollback')
print('Installed upgrade fixture passed:',phase)
