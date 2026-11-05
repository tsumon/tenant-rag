#!/usr/bin/env python3
"""Fetch pinned public retrieval source files, never models or inference APIs."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse, hashlib, json, urllib.request

SOURCES = [
 ('BeIR/scifact', 'b3b5335604bf5ee3c4447671af975ea25143d4f5', 'corpus/corpus-00000-of-00001.parquet'),
 ('BeIR/scifact', 'b3b5335604bf5ee3c4447671af975ea25143d4f5', 'queries/queries-00000-of-00001.parquet'),
 ('BeIR/scifact', 'b3b5335604bf5ee3c4447671af975ea25143d4f5', 'README.md'),
 ('BeIR/scifact-qrels', '2938d17dc3b09882fdb8c12bbbe2e2dc0e75a029', 'test.tsv'),
 ('BeIR/scifact-qrels', '2938d17dc3b09882fdb8c12bbbe2e2dc0e75a029', 'README.md'),
 ('C-MTEB/T2Retrieval', '8731a845f1bf500a4f111cf1070785c793d10e64', 'data/corpus-00000-of-00001-8afe7b7a7eca49e3.parquet'),
 ('C-MTEB/T2Retrieval', '8731a845f1bf500a4f111cf1070785c793d10e64', 'data/queries-00000-of-00001-930bf3b805a80dd9.parquet'),
 ('C-MTEB/T2Retrieval', '8731a845f1bf500a4f111cf1070785c793d10e64', 'README.md'),
 ('C-MTEB/T2Retrieval-qrels', '1c83b8d1544e529875e3f6930f3a1fcf749a8e97', 'data/dev-00000-of-00001-92ed0416056ff7e1.parquet'),
 ('C-MTEB/T2Retrieval-qrels', '1c83b8d1544e529875e3f6930f3a1fcf749a8e97', 'README.md'),
 ('mteb/T2Retrieval', '921dd3af6e78d1ae7ee0368aa8d7eaee02c8f08e', 'README.md'),
]
UPSTREAM = [
 ('allenai/scifact', '68b98a56d93e0f9da0d2aab4e6c3294699a0f72e', 'LICENSE.md'),
 ('allenai/scifact', '68b98a56d93e0f9da0d2aab4e6c3294699a0f72e', 'README.md'),
 ('THUIR/T2Ranking', '3ab0a0de72dd50bf84d852a985f6188334781403', 'README.md'),
]

EXPECTED_SHA256 = {'https://huggingface.co/datasets/BeIR/scifact/resolve/b3b5335604bf5ee3c4447671af975ea25143d4f5/corpus/corpus-00000-of-00001.parquet': '243324b35f03d82bd6d98a5f575966876e86cad7ce16e5333a35b1b793dc4f45', 'https://huggingface.co/datasets/BeIR/scifact/resolve/b3b5335604bf5ee3c4447671af975ea25143d4f5/queries/queries-00000-of-00001.parquet': '1c37956c5dc8b810b60302323c24d1a9e79e26411ba8f5ad9d0888642e2a9034', 'https://huggingface.co/datasets/BeIR/scifact/resolve/b3b5335604bf5ee3c4447671af975ea25143d4f5/README.md': 'b5f57be1839fdb75867073986a019a78afe0a149950f79d1165509efc5e5cf2f', 'https://huggingface.co/datasets/BeIR/scifact-qrels/resolve/2938d17dc3b09882fdb8c12bbbe2e2dc0e75a029/test.tsv': '0864bb985e0ca2367ba217977e72004d549054b2b06666ed9d4825ac7c21284c', 'https://huggingface.co/datasets/BeIR/scifact-qrels/resolve/2938d17dc3b09882fdb8c12bbbe2e2dc0e75a029/README.md': 'fd34a2830e557f1e32d46d0b34a218d280c9800ee4071579610d4ff587b4fd45', 'https://huggingface.co/datasets/C-MTEB/T2Retrieval/resolve/8731a845f1bf500a4f111cf1070785c793d10e64/data/corpus-00000-of-00001-8afe7b7a7eca49e3.parquet': 'd4a207a9277eb1cf31f68ab6a7236cbf3e8fbf9493e0e2b577a34660929fcd17', 'https://huggingface.co/datasets/C-MTEB/T2Retrieval/resolve/8731a845f1bf500a4f111cf1070785c793d10e64/data/queries-00000-of-00001-930bf3b805a80dd9.parquet': '47a5866f1e668700420f44b200f9808c9e799208a46da147dfe380b40fb406ec', 'https://huggingface.co/datasets/C-MTEB/T2Retrieval/resolve/8731a845f1bf500a4f111cf1070785c793d10e64/README.md': '9d6b94f8a11d6f772770b33a200448687248347e64f0332b5f3f2118a3fb0660', 'https://huggingface.co/datasets/C-MTEB/T2Retrieval-qrels/resolve/1c83b8d1544e529875e3f6930f3a1fcf749a8e97/data/dev-00000-of-00001-92ed0416056ff7e1.parquet': 'a283ce5c8bd739e7f5524107586eb21e68d7736172524b79eb5bd844c4b78c00', 'https://huggingface.co/datasets/C-MTEB/T2Retrieval-qrels/resolve/1c83b8d1544e529875e3f6930f3a1fcf749a8e97/README.md': 'fa17a7c92d0a6df560a19b41dba5e319b148c6d95e1a2de853c4754827371f1b', 'https://huggingface.co/datasets/mteb/T2Retrieval/resolve/921dd3af6e78d1ae7ee0368aa8d7eaee02c8f08e/README.md': 'd6c81643950dcd3d548f3a308119feea49b9a6d7d1d2579733a1aba6671a3243', 'https://raw.githubusercontent.com/allenai/scifact/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e/LICENSE.md': '56ee69bc639cbab8f0b2e4b67091ec5e9390aa99a1d65f0a35e4274686c9a806', 'https://raw.githubusercontent.com/allenai/scifact/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e/README.md': '3f0eb7f6d009b3785fede6592918b8b583e7193d26f14173fdf0c79aba1dcc79', 'https://raw.githubusercontent.com/THUIR/T2Ranking/3ab0a0de72dd50bf84d852a985f6188334781403/README.md': 'e75fe58450516cdb76ce49b18be9d5685490f41440e246bb37fbd2543b96f73f'}

def main():
 p=argparse.ArgumentParser(); p.add_argument('--output', type=Path, default=Path(__file__).parent); args=p.parse_args(); root=args.output
 previous=json.loads((root/'SOURCE_FILES.json').read_text()) if (root/'SOURCE_FILES.json').exists() else []
 checksums={x['url']:x['sha256'] for x in previous}
 def get(item):
  repo,rev,name=item
  if item in UPSTREAM:
   url=f'https://raw.githubusercontent.com/{repo}/{rev}/{name}'
   path=root/'provenance'/(repo.replace('/','--')+'--'+name)
  else:
   url=f'https://huggingface.co/datasets/{repo}/resolve/{rev}/{name}'
   path=root/'raw'/repo/name
  path.parent.mkdir(parents=True,exist_ok=True)
  if not path.exists():
   partial=path.with_suffix(path.suffix+'.partial')
   with urllib.request.urlopen(url, timeout=120) as src, partial.open('wb') as out:
    while chunk:=src.read(1024*1024): out.write(chunk)
   partial.replace(path)
  digest=hashlib.sha256(path.read_bytes()).hexdigest()
  expected=EXPECTED_SHA256[url]
  if digest!=expected: raise RuntimeError(f'Checksum mismatch: {path}')
  if url in checksums and checksums[url]!=digest: raise RuntimeError(f'Existing receipt mismatch: {path}')
  record={'repository':repo,'revision':rev,'source_path':name,'url':url,'local_path':str(path.relative_to(root)),'bytes':path.stat().st_size,'sha256':digest}
  print(json.dumps(record),flush=True); return record
 with ThreadPoolExecutor(max_workers=4) as pool: records=list(pool.map(get,SOURCES+UPSTREAM))
 (root/'SOURCE_FILES.json').write_text(json.dumps(records,indent=2,ensure_ascii=False)+'\n')
if __name__=='__main__': main()
