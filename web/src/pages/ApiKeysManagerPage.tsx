import { useEffect, useState } from 'react';
import { KeyRound, Plus, RefreshCw, ShieldOff } from 'lucide-react';
import { adminApi } from '../api';
import type { ApiKeyRecord, TenantRecord } from '../types';
import { Badge, Card, CopyButton, Modal, formatTime } from '../components/ui';

const ALL_SCOPES = ['read', 'write', 'chat', 'eval', 'tenant_admin'];

export function ApiKeysManagerPage({
  adminToken,
  onToast,
}: {
  adminToken: string;
  onToast: (msg: string) => void;
}) {
  const [tenants, setTenants] = useState<TenantRecord[]>([]);
  const [keys, setKeys] = useState<ApiKeyRecord[]>([]);
  const [filterTenant, setFilterTenant] = useState('');
  const [issueOpen, setIssueOpen] = useState(false);
  const [revealedKey, setRevealedKey] = useState<ApiKeyRecord | null>(null);
  const [issueForm, setIssueForm] = useState({
    tenant_id: '',
    name: 'prod-service-key',
    scopes: ['read', 'write', 'chat', 'eval'] as string[],
    expires_in_days: 90,
  });

  const load = async () => {
    if (!adminToken) return;
    try {
      const [ov, ks] = await Promise.all([
        adminApi.overview(adminToken),
        adminApi.listKeys(adminToken, filterTenant || undefined),
      ]);
      setTenants(ov.tenants);
      setKeys(ks.keys);
      if (!issueForm.tenant_id && ov.tenants.length > 0) {
        setIssueForm((prev) => ({ ...prev, tenant_id: ov.tenants[0].tenant_id }));
      }
    } catch (err) {
      onToast(`加载密钥失败：${(err as Error).message}`);
    }
  };

  useEffect(() => {
    void load();
  }, [adminToken, filterTenant]);

  const toggleScope = (scope: string) => {
    setIssueForm((prev) => {
      const exists = prev.scopes.includes(scope);
      const next = exists ? prev.scopes.filter((s) => s !== scope) : [...prev.scopes, scope];
      return { ...prev, scopes: next };
    });
  };

  const handleIssue = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!issueForm.scopes.length) {
      onToast('至少选择一个权限 Scope');
      return;
    }
    try {
      const res = await adminApi.issueKey(adminToken, issueForm.tenant_id, {
        name: issueForm.name,
        scopes: issueForm.scopes,
        expires_in_days: issueForm.expires_in_days > 0 ? issueForm.expires_in_days : null,
      });
      setIssueOpen(false);
      setRevealedKey(res.key);
      onToast('已签发新的作用域 API Key');
      await load();
    } catch (err) {
      onToast(`签发失败：${(err as Error).message}`);
    }
  };

  const handleRotate = async (k: ApiKeyRecord) => {
    try {
      const res = await adminApi.rotateKey(adminToken, k.key_id, { grace_seconds: 3600 });
      setRevealedKey(res.new_key);
      onToast(`密钥已平滑轮换，旧 Key 将在 1 小时宽限期后自动失效`);
      await load();
    } catch (err) {
      onToast(`轮换失败：${(err as Error).message}`);
    }
  };

  const handleRevoke = async (k: ApiKeyRecord) => {
    if (!window.confirm(`确认立即吊销密钥 ${k.name} (${k.key_prefix})？`)) return;
    try {
      await adminApi.revokeKey(adminToken, k.key_id);
      onToast('密钥已吊销');
      await load();
    } catch (err) {
      onToast(`吊销失败：${(err as Error).message}`);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Scoped API Key 生命周期管理</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            基于 SHA-256 哈希保存，支持读写权限 Scope 隔离、过期 TTL、零停机平滑轮换与即时吊销
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={filterTenant}
            onChange={(e) => setFilterTenant(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-900"
          >
            <option value="">全部租户</option>
            {tenants.map((t) => (
              <option key={t.tenant_id} value={t.tenant_id}>
                {t.tenant_id}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setIssueOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-blue-500"
          >
            <Plus className="h-4 w-4" /> 签发新 Key
          </button>
        </div>
      </div>

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 text-slate-500 dark:border-slate-800">
                <th className="py-2.5 pr-4">名称 / 前缀</th>
                <th className="py-2.5 pr-4">归属租户</th>
                <th className="py-2.5 pr-4">权限 Scopes</th>
                <th className="py-2.5 pr-4">状态</th>
                <th className="py-2.5 pr-4">最近使用 / 过期时间</th>
                <th className="py-2.5">生命周期操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
              {keys.map((k) => (
                <tr key={k.key_id}>
                  <td className="py-3 pr-4">
                    <div className="flex items-center gap-1.5 font-semibold">
                      <KeyRound className="h-3.5 w-3.5 text-blue-500" />
                      {k.name}
                    </div>
                    <div className="font-mono text-[11px] text-slate-500">{k.key_prefix}</div>
                  </td>
                  <td className="py-3 pr-4 font-mono">{k.tenant_id}</td>
                  <td className="py-3 pr-4">
                    <div className="flex flex-wrap gap-1">
                      {k.scopes.map((s) => (
                        <Badge key={s} variant={s === 'tenant_admin' ? 'purple' : 'blue'}>
                          {s}
                        </Badge>
                      ))}
                    </div>
                  </td>
                  <td className="py-3 pr-4">
                    <Badge
                      variant={
                        k.status === 'active' ? 'emerald' : k.status === 'expired' ? 'amber' : 'rose'
                      }
                    >
                      {k.status}
                    </Badge>
                  </td>
                  <td className="py-3 pr-4 text-[11px] text-slate-500 tabular-nums">
                    <div>使用: {formatTime(k.last_used_at)}</div>
                    <div>过期: {k.expires_at ? formatTime(k.expires_at) : '永不过期'}</div>
                  </td>
                  <td className="py-3">
                    {k.status === 'active' ? (
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => void handleRotate(k)}
                          className="inline-flex items-center gap-1 rounded-md border border-blue-500/40 bg-blue-500/10 px-2.5 py-1 text-[11px] font-medium text-blue-600 hover:bg-blue-500/20 dark:text-blue-300"
                        >
                          <RefreshCw className="h-3 w-3" /> 平滑轮换
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleRevoke(k)}
                          className="inline-flex items-center gap-1 rounded-md border border-rose-500/40 px-2.5 py-1 text-[11px] font-medium text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                        >
                          <ShieldOff className="h-3 w-3" /> 吊销
                        </button>
                      </div>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Issue Modal */}
      <Modal open={issueOpen} title="签发作用域 API Key" onClose={() => setIssueOpen(false)}>
        <form onSubmit={handleIssue} className="space-y-3 text-xs">
          <div>
            <label className="mb-1 block font-medium text-slate-600 dark:text-slate-300">目标租户</label>
            <select
              value={issueForm.tenant_id}
              onChange={(e) => setIssueForm({ ...issueForm, tenant_id: e.target.value })}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono dark:border-slate-700 dark:bg-slate-950"
            >
              {tenants.map((t) => (
                <option key={t.tenant_id} value={t.tenant_id}>
                  {t.tenant_id} ({t.display_name})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block font-medium text-slate-600 dark:text-slate-300">密钥备注名称</label>
            <input
              required
              value={issueForm.name}
              onChange={(e) => setIssueForm({ ...issueForm, name: e.target.value })}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
            />
          </div>
          <div>
            <label className="mb-1.5 block font-medium text-slate-600 dark:text-slate-300">权限 Scopes</label>
            <div className="flex flex-wrap gap-2">
              {ALL_SCOPES.map((s) => {
                const checked = issueForm.scopes.includes(s);
                return (
                  <button
                    key={s}
                    type="button"
                    onClick={() => toggleScope(s)}
                    className={`rounded-lg border px-2.5 py-1.5 font-mono transition ${
                      checked
                        ? 'border-blue-500 bg-blue-500/15 text-blue-600 dark:text-blue-300'
                        : 'border-slate-300 text-slate-500 dark:border-slate-700'
                    }`}
                  >
                    {s}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <label className="mb-1 block font-medium text-slate-600 dark:text-slate-300">有效期（天，0 表示永久）</label>
            <input
              type="number"
              min={0}
              value={issueForm.expires_in_days}
              onChange={(e) => setIssueForm({ ...issueForm, expires_in_days: Number(e.target.value) })}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setIssueOpen(false)} className="rounded-lg border border-slate-300 px-3 py-2 dark:border-slate-700">
              取消
            </button>
            <button type="submit" className="rounded-lg bg-blue-600 px-4 py-2 font-semibold text-white hover:bg-blue-500">
              签发密钥
            </button>
          </div>
        </form>
      </Modal>

      {/* One-time Secret Reveal Modal */}
      <Modal
        open={Boolean(revealedKey)}
        title="一次性明文密钥凭证（请妥善保存）"
        onClose={() => setRevealedKey(null)}
      >
        {revealedKey && (
          <div className="space-y-3 text-xs">
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-amber-700 dark:text-amber-300">
              服务端仅保存 SHA-256 哈希，关闭弹窗后无法再次查看明文 API Key。
            </div>
            <div className="flex items-center justify-between gap-2 rounded-lg border border-slate-300 bg-slate-50 p-3 font-mono text-xs dark:border-slate-700 dark:bg-slate-950">
              <span className="break-all select-all">{revealedKey.api_key}</span>
              <CopyButton text={revealedKey.api_key || ''} />
            </div>
            <div className="rounded-lg bg-slate-950 p-3 text-[11px] text-slate-300">
              <div className="mb-1 font-semibold text-slate-400">cURL 调用示例</div>
              <pre className="overflow-x-auto">
{`curl -sS http://127.0.0.1:8080/api/v1/search \\
  -H "Authorization: Bearer ${revealedKey.api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{"query":"SLA可用性","k":5,"reranker":"lexical"}'`}
              </pre>
            </div>
            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => setRevealedKey(null)}
                className="rounded-lg bg-blue-600 px-4 py-2 font-semibold text-white"
              >
                我已保存，关闭
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
