"""Run with installed package using python -I, outside source imports."""
import sqlite3
import tempfile
from pathlib import Path
from tenantrag import TenantRAG, FakeEmbedder, EmbeddingIdentityError, ReindexCancelled

with tempfile.TemporaryDirectory() as root:
    db = str(Path(root) / 'smoke.db')
    with TenantRAG(db) as rag:
        rag.register('a', 'release-key-a', quota_docs=1)
        rag.register('b', 'release-key-b', quota_docs=1)
        result = rag.ingest('release-key-a', 'alpha beta gamma delta', 'parent',
                            source='guide.md', max_chars=10, overlap=2)
        assert result['chunks'] > 1
        assert rag.ingest('release-key-a', 'alpha beta gamma delta', 'parent',
                          source='guide.md', max_chars=10, overlap=2)['unchanged']
        assert rag.usage('release-key-a')['docs'] == 1
        assert not rag.search('release-key-b', 'alpha')
        assert rag.search('release-key-a', 'alpha')[0]['metadata']['_chunk']['source'] == 'guide.md'
        try:
            rag.reindex(FakeEmbedder(dim=128), cancelled=lambda: True)
        except ReindexCancelled:
            pass
        else:
            raise AssertionError('cancellation was ignored')
        assert rag.reindex(FakeEmbedder(dim=128)) == result['chunks']
    with TenantRAG(db, FakeEmbedder(dim=128)) as rag:
        assert rag.search('release-key-a', 'alpha')
        assert rag.delete('release-key-a', 'parent')
        assert rag.usage('release-key-a')['docs'] == 0
        rag.index('release-key-a', 'legacy provenance check', 'legacy')
    # Simulate a restored unlabelled pre-v0.4 database without changing data.
    with sqlite3.connect(db) as con:
        con.execute('DELETE FROM embedding_config')
    with TenantRAG(db, FakeEmbedder(dim=128)) as rag:
        try:
            rag.search('release-key-a', 'legacy')
        except EmbeddingIdentityError:
            pass
        else:
            raise AssertionError('unlabelled legacy vectors were accepted')
        rag.adopt_legacy_embeddings()
        assert rag.search('release-key-a', 'legacy')
print('Installed v0.4 ingestion, isolation, reindex, cancellation and legacy adoption passed')
