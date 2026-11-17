"""Enterprise dual-mode layer for tenant-rag.

Provides Control Plane IAM, physical per-tenant SQLite WAL pooling,
pre-retrieval metadata/ACL filtering, 2-stage reranking, parent-child context
expansion, grounded citation chat, multi-format parsing, async ingestion jobs,
online hot backup/restore, and Prometheus observability while leaving the core
zero-dependency ``tenantrag`` SDK untouched.
"""

from .errors import (
    EnterpriseError,
    UnauthorizedError,
    ForbiddenError,
    NotFoundError,
    ConflictError,
)
from .control_plane import ControlPlane, VALID_SCOPES, validate_tenant_id
from .tenant_pool import TenantPool, TenantNode
from .rerank import NoOpReranker, LexicalCrossReranker, APIReranker
from .retrieval import enterprise_search, evaluate_acl, evaluate_filter, validate_filters
from .parsers import parse_document_bytes, SUPPORTED_EXTENSIONS
from .jobs import AsyncJobManager
from .chat import GroundedChatEngine
from .observability import MetricsRegistry
from .engine import EnterpriseTenantRAG

__all__ = [
    "EnterpriseError",
    "UnauthorizedError",
    "ForbiddenError",
    "NotFoundError",
    "ConflictError",
    "ControlPlane",
    "VALID_SCOPES",
    "validate_tenant_id",
    "TenantPool",
    "TenantNode",
    "NoOpReranker",
    "LexicalCrossReranker",
    "APIReranker",
    "enterprise_search",
    "evaluate_acl",
    "evaluate_filter",
    "validate_filters",
    "parse_document_bytes",
    "SUPPORTED_EXTENSIONS",
    "AsyncJobManager",
    "GroundedChatEngine",
    "MetricsRegistry",
    "EnterpriseTenantRAG",
]
