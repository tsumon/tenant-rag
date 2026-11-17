export type Perspective = 'admin' | 'tenant';

export type AdminTab = 'overview' | 'tenants' | 'keys' | 'audit';
export type TenantTab = 'kb' | 'search' | 'chat' | 'eval' | 'isolation';

export interface TenantStats {
  docs: number;
  parent_docs?: number;
  single_docs?: number;
  chunks: number;
  db_bytes: number;
  db_path?: string;
  cache_generation?: number;
  model_identity?: {
    provider: string;
    model: string;
    revision: string;
    dimension: number;
  } | null;
}

export interface TenantRecord {
  tenant_id: string;
  display_name: string;
  status: 'active' | 'suspended';
  isolation_mode: string;
  quota_docs: number;
  quota_chunks: number;
  rate_limit_rpm: number;
  description: string;
  created_at: number;
  updated_at: number;
  stats: TenantStats;
}

export interface ApiKeyRecord {
  key_id: string;
  tenant_id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  status: 'active' | 'revoked' | 'expired';
  expires_at: number | null;
  revoked_at: number | null;
  rotated_from: string | null;
  last_used_at: number | null;
  created_at: number;
  api_key?: string;
}

export interface AuditRecord {
  audit_id: number;
  request_id: string | null;
  timestamp: number;
  actor_type: string;
  actor_id: string;
  tenant_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  status: string;
  latency_ms: number | null;
  detail: Record<string, unknown>;
  prev_hash: string;
  entry_hash: string;
}

export interface AuditVerifyResult {
  valid: boolean;
  checked: number;
  total: number;
  broken_at: number | null;
  reason?: string;
  head_hash?: string;
}

export interface BackupRecord {
  backup_id: string;
  tenant_id: string;
  file_path: string;
  size_bytes: number;
  sha256: string;
  integrity_ok: boolean;
  doc_count: number;
  created_at: number;
}

export interface AdminOverview {
  tenants: TenantRecord[];
  totals: {
    tenants: number;
    active_tenants: number;
    docs: number;
    chunks: number;
    storage_bytes: number;
    jobs_recent: number;
    backups: number;
  };
  metrics: {
    uptime_seconds: number;
    total_requests: number;
    total_searches: number;
    total_abstentions: number;
    total_ingestions: number;
    rate_limited_total: number;
    auth_denied_total: number;
    latency_p50_ms: number;
    latency_p95_ms: number;
  };
  embedder: Record<string, unknown>;
  llm_configured: boolean;
}

export interface DocumentItem {
  doc_id: string;
  text: string;
  metadata: Record<string, unknown>;
  created_at: number;
  source: string | null;
  kind: 'parent' | 'single';
  chunk_count: number;
}

export interface ChunkDetail {
  chunk_id: string;
  index: number;
  start: number;
  end: number;
  text: string;
}

export interface DocumentDetail {
  doc_id: string;
  kind: 'parent' | 'single';
  text: string;
  metadata: Record<string, unknown>;
  source: string | null;
  content_hash?: string;
  options?: Record<string, unknown>;
  created_at: number;
  chunks: ChunkDetail[];
}

export interface IngestionJob {
  job_id: string;
  tenant_id: string;
  job_type: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  filename: string | null;
  file_format: string | null;
  total_docs: number;
  processed_docs: number;
  total_chunks: number;
  progress_pct: number;
  result: Record<string, unknown> | null;
  error_message: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface SearchHit {
  doc_id: string;
  text: string;
  metadata: Record<string, unknown>;
  score: number;
  vector_score?: number | null;
  fts_bm25?: number | null;
  stage1_rank?: number;
  stage1_score?: number;
  cross_score?: number;
  rerank_score?: number;
  rerank_rank?: number;
  rank_delta?: number;
  matched: string[];
  expanded_text?: string;
  expanded_start?: number;
  expanded_end?: number;
  parent_source?: string | null;
}

export interface SearchDiagnostics {
  timings_ms: Record<string, number>;
  fts_query: string | null;
  corpus_chunks: number;
  allowed_candidates: number;
  filtered_out_acl: number;
  filtered_out_metadata: number;
  vector_top: Array<{ doc_id: string; cosine: number }>;
  fts_top: Array<{ doc_id: string; bm25: number }>;
}

export interface SearchResponse {
  hits: SearchHit[];
  total_candidates: number;
  evidence_sufficient: boolean;
  abstention_reason: string | null;
  latency_ms: number;
  reranker: string;
  fusion: string;
  diagnostics?: SearchDiagnostics;
}

export interface CitationItem {
  index: number;
  doc_id: string;
  parent_id: string;
  source: string;
  start: number;
  end: number;
  score: number;
  vector_score: number | null;
  matched: string[];
  quote: string;
}

export interface DemoBootstrapData {
  demo_mode: boolean;
  admin_token: string;
  tenants: Array<{
    tenant_id: string;
    display_name: string;
    api_key: string;
    docs: number;
    chunks: number;
  }>;
}

export interface EvalSummaryMode {
  label: string;
  recall_at_k: number;
  mrr_at_k: number;
  avg_latency_ms: number;
}

export interface EvalRunResponse {
  dataset: string;
  k: number;
  query_count: number;
  embedder: Record<string, unknown>;
  summary: Record<string, EvalSummaryMode>;
  queries: Array<{
    id: string;
    query: string;
    expected: string[];
    suite: string;
    results: Record<string, { ranked: string[]; recall: number; mrr: number }>;
  }>;
}
