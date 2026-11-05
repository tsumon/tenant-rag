"""tenantrag: 多租户混合检索（RAG + grep）。

定位：给独立开发者个人 AI 应用用的轻量检索底座，不是另一个 Elasticsearch。
核心：向量检索 + FTS5 全文检索 → RRF 融合；所有数据按 tenant_id 强隔离。
"""
from .service import IngestionConflictError, TenantRAG, AuthError, QuotaError
from .store import Store, IsolationError
from .embed import Embedder, FakeEmbedder, OpenAIEmbedder
from .chunking import chunk_text
from .identity import EmbeddingIdentityError, ReindexConflictError, ReindexCancelled
from .search import hybrid_search, build_fts_query, rrf

__all__ = [
    "chunk_text", "EmbeddingIdentityError", "ReindexConflictError", "ReindexCancelled",
    "IngestionConflictError", "TenantRAG", "AuthError", "QuotaError",
    "Store", "IsolationError",
    "Embedder", "FakeEmbedder", "OpenAIEmbedder",
    "hybrid_search", "build_fts_query", "rrf",
]
