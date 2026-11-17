import type {
  AdminOverview,
  ApiKeyRecord,
  AuditRecord,
  AuditVerifyResult,
  BackupRecord,
  DemoBootstrapData,
  DocumentDetail,
  DocumentItem,
  EvalRunResponse,
  IngestionJob,
  SearchResponse,
  TenantStats,
} from './types';

async function requestJson<T>(
  url: string,
  options: {
    method?: string;
    token?: string | null;
    adminToken?: string | null;
    body?: unknown;
    formData?: FormData;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.token) headers['Authorization'] = `Bearer ${options.token}`;
  if (options.adminToken) headers['X-Admin-Token'] = options.adminToken;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';

  const res = await fetch(url, {
    method: options.method ?? 'GET',
    headers,
    body: options.formData
      ? options.formData
      : options.body !== undefined
        ? JSON.stringify(options.body)
        : undefined,
  });

  let data: Record<string, unknown> | null = null;
  try {
    data = (await res.json()) as Record<string, unknown>;
  } catch {
    data = null;
  }

  if (!res.ok) {
    const detailObj = (data?.detail as Record<string, unknown> | undefined) ?? data;
    const msg =
      (detailObj?.hint as string) ||
      (detailObj?.error as string) ||
      (data?.hint as string) ||
      (data?.error as string) ||
      `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data as T;
}

export async function fetchHealth() {
  return requestJson<{ ok: boolean; version: string; demo_mode: boolean }>('/api/v1/health');
}

export async function fetchDemoBootstrap() {
  return requestJson<DemoBootstrapData>('/api/v1/admin/demo/bootstrap');
}

export async function fetchMetricsText() {
  const res = await fetch('/metrics');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.text();
}

// ---- Admin API ----
export const adminApi = {
  overview: (adminToken: string) =>
    requestJson<AdminOverview>('/api/v1/admin/overview', { adminToken }),
  createTenant: (
    adminToken: string,
    payload: {
      tenant_id: string;
      display_name?: string;
      quota_docs: number;
      quota_chunks: number;
      rate_limit_rpm: number;
      description?: string;
    },
  ) =>
    requestJson<{ tenant: unknown }>('/api/v1/admin/tenants', {
      method: 'POST',
      adminToken,
      body: payload,
    }),
  updateTenant: (
    adminToken: string,
    tenantId: string,
    payload: Record<string, unknown>,
  ) =>
    requestJson<{ tenant: unknown }>(`/api/v1/admin/tenants/${encodeURIComponent(tenantId)}`, {
      method: 'PATCH',
      adminToken,
      body: payload,
    }),
  deleteTenant: (adminToken: string, tenantId: string) =>
    requestJson<{ deleted: boolean }>(`/api/v1/admin/tenants/${encodeURIComponent(tenantId)}`, {
      method: 'DELETE',
      adminToken,
    }),
  listKeys: (adminToken: string, tenantId?: string) =>
    requestJson<{ keys: ApiKeyRecord[] }>(
      tenantId ? `/api/v1/admin/keys?tenant_id=${encodeURIComponent(tenantId)}` : '/api/v1/admin/keys',
      { adminToken },
    ),
  issueKey: (
    adminToken: string,
    tenantId: string,
    payload: { name: string; scopes: string[]; expires_in_days?: number | null },
  ) =>
    requestJson<{ key: ApiKeyRecord }>(
      `/api/v1/admin/tenants/${encodeURIComponent(tenantId)}/keys`,
      { method: 'POST', adminToken, body: payload },
    ),
  rotateKey: (
    adminToken: string,
    keyId: string,
    payload: { grace_seconds: number; name?: string },
  ) =>
    requestJson<{ new_key: ApiKeyRecord; previous_key: ApiKeyRecord; grace_seconds: number }>(
      `/api/v1/admin/keys/${encodeURIComponent(keyId)}/rotate`,
      { method: 'POST', adminToken, body: payload },
    ),
  revokeKey: (adminToken: string, keyId: string) =>
    requestJson<{ key: ApiKeyRecord }>(`/api/v1/admin/keys/${encodeURIComponent(keyId)}/revoke`, {
      method: 'POST',
      adminToken,
    }),
  listBackups: (adminToken: string, tenantId?: string) =>
    requestJson<{ backups: BackupRecord[] }>(
      tenantId
        ? `/api/v1/admin/backups?tenant_id=${encodeURIComponent(tenantId)}`
        : '/api/v1/admin/backups',
      { adminToken },
    ),
  createBackup: (adminToken: string, tenantId: string) =>
    requestJson<{ backup: BackupRecord }>(
      `/api/v1/admin/tenants/${encodeURIComponent(tenantId)}/backups`,
      { method: 'POST', adminToken },
    ),
  restoreBackup: (adminToken: string, tenantId: string, backupId: string) =>
    requestJson<{ restored: boolean; stats: TenantStats }>(
      `/api/v1/admin/tenants/${encodeURIComponent(tenantId)}/backups/${encodeURIComponent(backupId)}/restore`,
      { method: 'POST', adminToken },
    ),
  queryAudit: (
    adminToken: string,
    params: { tenant_id?: string; action?: string; status?: string; limit?: number } = {},
  ) => {
    const qs = new URLSearchParams();
    if (params.tenant_id) qs.set('tenant_id', params.tenant_id);
    if (params.action) qs.set('action', params.action);
    if (params.status) qs.set('status', params.status);
    qs.set('limit', String(params.limit ?? 100));
    return requestJson<{ logs: AuditRecord[] }>(`/api/v1/admin/audit?${qs.toString()}`, {
      adminToken,
    });
  },
  verifyAudit: (adminToken: string) =>
    requestJson<AuditVerifyResult>('/api/v1/admin/audit/verify', { adminToken }),
};

// ---- Tenant API ----
export const tenantApi = {
  whoami: (token: string) =>
    requestJson<{
      tenant_id: string;
      display_name: string;
      key_prefix: string;
      scopes: string[];
      quota_docs: number;
      quota_chunks: number;
      rate_limit_rpm: number;
      stats: TenantStats;
      llm_configured: boolean;
    }>('/api/v1/whoami', { token }),
  listDocs: (token: string, limit = 100, offset = 0) =>
    requestJson<{ docs: DocumentItem[]; stats: TenantStats }>(
      `/api/v1/docs?limit=${limit}&offset=${offset}`,
      { token },
    ),
  getDoc: (token: string, docId: string) =>
    requestJson<DocumentDetail>(`/api/v1/docs/${encodeURIComponent(docId)}`, { token }),
  deleteDoc: (token: string, docId: string) =>
    requestJson<{ deleted: boolean }>(`/api/v1/docs/${encodeURIComponent(docId)}`, {
      method: 'DELETE',
      token,
    }),
  ingestText: (
    token: string,
    payload: {
      text: string;
      doc_id?: string;
      source?: string;
      format?: string;
      max_chars?: number;
      overlap?: number;
      metadata?: Record<string, unknown>;
      acl?: Record<string, unknown>;
    },
  ) =>
    requestJson<{ doc_id: string; chunks: number; unchanged: boolean }>('/api/v1/ingest', {
      method: 'POST',
      token,
      body: payload,
    }),
  ingestFile: (token: string, formData: FormData) =>
    requestJson<{ mode: string; job?: IngestionJob; document?: Record<string, unknown> }>(
      '/api/v1/ingest/files',
      { method: 'POST', token, formData },
    ),
  listJobs: (token: string) => requestJson<{ jobs: IngestionJob[] }>('/api/v1/jobs', { token }),
  getJob: (token: string, jobId: string) =>
    requestJson<IngestionJob>(`/api/v1/jobs/${encodeURIComponent(jobId)}`, { token }),
  search: (token: string, payload: Record<string, unknown>) =>
    requestJson<SearchResponse>('/api/v1/search', {
      method: 'POST',
      token,
      body: payload,
    }),
  runEval: (token: string, payload: { suite: string; k: number }) =>
    requestJson<EvalRunResponse>('/api/v1/eval/run', {
      method: 'POST',
      token,
      body: payload,
    }),
};

export async function streamChat(
  token: string,
  payload: Record<string, unknown>,
  handlers: {
    onRetrieval?: (data: Record<string, unknown>) => void;
    onDelta?: (text: string) => void;
    onCitations?: (data: Record<string, unknown>) => void;
    onDone?: (data: Record<string, unknown>) => void;
  },
) {
  const res = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (!res.ok || !res.body) {
    let errText = `HTTP ${res.status}`;
    try {
      const errJson = (await res.json()) as Record<string, unknown>;
      errText = (errJson.hint as string) || (errJson.error as string) || errText;
    } catch {
      // ignore
    }
    throw new Error(errText);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  const dispatchBlock = (block: string) => {
    let eventName = 'message';
    const dataLines: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim());
      }
    }
    if (!dataLines.length) return;
    const parsed = JSON.parse(dataLines.join('\n')) as Record<string, unknown>;
    if (eventName === 'retrieval') handlers.onRetrieval?.(parsed);
    else if (eventName === 'delta') handlers.onDelta?.(String(parsed.text ?? ''));
    else if (eventName === 'citations') handlers.onCitations?.(parsed);
    else if (eventName === 'done') handlers.onDone?.(parsed);
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx = buffer.indexOf('\n\n');
    while (idx !== -1) {
      const block = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (block) dispatchBlock(block);
      idx = buffer.indexOf('\n\n');
    }
  }
  if (buffer.trim()) {
    dispatchBlock(buffer.trim());
  }
}
