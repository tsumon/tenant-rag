"""Run using the installed wheel interpreter from outside the checkout."""
import json
from importlib.resources import files
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from tenantrag import TenantRAG
from tenantrag.server import make_server
from admin.admin_server import AdminHandler
assert files('admin').joinpath('static/index.html').is_file()
with tempfile.TemporaryDirectory() as tmp:
    path=tmp+'/db.sqlite'
    with TenantRAG(path) as rag:
        rag.register('smoke','smoke-test-key')
    api=make_server(path,port=0)
    worker=threading.Thread(target=api.serve_forever,daemon=True);worker.start()
    ui=ThreadingHTTPServer(('127.0.0.1',0),AdminHandler)
    ui.api_base='http://127.0.0.1:'+str(api.server_address[1])
    ui_worker=threading.Thread(target=ui.serve_forever,daemon=True);ui_worker.start()
    base='http://127.0.0.1:'+str(ui.server_address[1])
    def request(method,path,payload=None):
        req=urllib.request.Request(base+path,method=method,
            data=json.dumps(payload).encode() if payload else None,
            headers={'Authorization':'Bearer smoke-test-key','Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
    try:
        with urllib.request.urlopen(base+'/',timeout=5) as response:
            assert b'btn-upload' in response.read()
        assert request('POST','/v1/index',{'doc_id':'d','text':'release smoke test'})['doc_id']=='d'
        assert request('POST','/v1/search',{'query':'release'})['hits'][0]['doc_id']=='d'
        for weight in (0,0.2):
            assert request('POST','/v1/search',{'query':'release','fusion':'score','lexical_weight':weight})['hits'][0]['doc_id']=='d'
        assert request('GET','/v1/docs')['docs'][0]['doc_id']=='d'
        assert request('DELETE','/v1/docs/d')['deleted']
        assert request('GET','/v1/usage')['docs']==0
    finally:
        ui.shutdown();ui.server_close();ui_worker.join()
        api.shutdown();api.server_close();worker.join();api.rag.close()
print('Installed wheel HTTP + admin proxy + HTML roundtrip passed')
