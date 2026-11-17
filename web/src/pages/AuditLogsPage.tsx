import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldCheck } from 'lucide-react';
import { adminApi } from '../api';
import type { AuditRecord, AuditVerifyResult } from '../types';
import { Badge, Card, formatTime } from '../components/ui';

export function AuditLogsPage({
  adminToken,
  onToast,
}: {
  adminToken: string;
  onToast: (msg: string) => void;
}) {
  const [logs, setLogs] = useState<AuditRecord[]>([]);
  const [tenantFilter, setTenantFilter] = useState('');
  const [actionFilter, setActionFilter] = useState('');
  const [verifyRes, setVerifyRes] = useState<AuditVerifyResult | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!adminToken) return;
    setLoading(true);
    try {
      const res = await adminApi.queryAudit(adminToken, {
        tenant_id: tenantFilter || undefined,
        action: actionFilter || undefined,
        limit: 80,
      });
      setLogs(res.logs);
    } catch (err) {
      onToast(`查询审计日志失败：${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [adminToken]);

  const handleVerify = async () => {
    try {
      const res = await adminApi.verifyAudit(adminToken);
      setVerifyRes(res);
      onToast(res.valid ? `哈希链完整通过（共核验 ${res.checked} 条）` : `警告：审计链在第 #${res.broken_at} 条断裂！`);
    } catch (err) {
      onToast(`核验失败：${(err as Error).message}`);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">防篡改全链路审计中心</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            每条审计记录携带 prev_hash 与 SHA-256 entry_hash，支持一键全链防篡改验证
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void handleVerify()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-emerald-500"
          >
            <ShieldCheck className="h-4 w-4" /> 校验 SHA-256 审计哈希链
          </button>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium dark:border-slate-700 dark:bg-slate-800"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /> 刷新
          </button>
        </div>
      </div>

      {verifyRes && (
        <div
          className={`flex items-center justify-between rounded-xl border p-4 text-xs ${
            verifyRes.valid
              ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
              : 'border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300'
          }`}
        >
          <div className="flex items-center gap-2.5">
            {verifyRes.valid ? <CheckCircle2 className="h-5 w-5" /> : <AlertTriangle className="h-5 w-5" />}
            <div>
              <div className="font-semibold">
                {verifyRes.valid
                  ? `审计哈希链完整性核验通过（共校验 ${verifyRes.checked} 条连续记录）`
                  : `检测到审计日志篡改！断裂位置 audit_id=${verifyRes.broken_at}（${verifyRes.reason}）`}
              </div>
              {verifyRes.head_hash && (
                <div className="mt-0.5 font-mono text-[11px] opacity-85">
                  Head SHA-256: {verifyRes.head_hash}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <Card>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <input
            value={tenantFilter}
            onChange={(e) => setTenantFilter(e.target.value)}
            placeholder="按 tenant_id 过滤…"
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
          />
          <input
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            placeholder="按 action 过滤（如 doc.search）…"
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
          />
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-lg bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-500"
          >
            筛选
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 text-slate-500 dark:border-slate-800">
                <th className="py-2 pr-3">#ID</th>
                <th className="py-2 pr-3">时间</th>
                <th className="py-2 pr-3">操作者 / 租户</th>
                <th className="py-2 pr-3">动作</th>
                <th className="py-2 pr-3">状态</th>
                <th className="py-2 pr-3">摘要细节</th>
                <th className="py-2">Entry Hash</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
              {logs.map((l) => (
                <tr key={l.audit_id} className="hover:bg-slate-50/70 dark:hover:bg-slate-800/30">
                  <td className="py-2.5 pr-3 font-mono text-slate-500">#{l.audit_id}</td>
                  <td className="py-2.5 pr-3 text-[11px] tabular-nums text-slate-500">{formatTime(l.timestamp)}</td>
                  <td className="py-2.5 pr-3">
                    <div className="font-mono text-[11px]">{l.actor_id}</div>
                    <div className="text-[11px] text-slate-500">{l.tenant_id || 'platform'}</div>
                  </td>
                  <td className="py-2.5 pr-3">
                    <Badge variant="blue" className="font-mono">{l.action}</Badge>
                  </td>
                  <td className="py-2.5 pr-3">
                    <Badge variant={l.status === 'ok' ? 'emerald' : 'amber'}>{l.status}</Badge>
                  </td>
                  <td className="py-2.5 pr-3 max-w-xs truncate font-mono text-[11px] text-slate-600 dark:text-slate-400">
                    {JSON.stringify(l.detail)}
                  </td>
                  <td className="py-2.5 font-mono text-[11px] text-slate-500">
                    {l.entry_hash.slice(0, 14)}…
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
