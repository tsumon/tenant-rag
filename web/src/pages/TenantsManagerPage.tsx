import { useEffect, useState } from 'react';
import {
  Archive,
  HardDrive,
  PauseCircle,
  PlayCircle,
  Plus,
  RotateCcw,
  Trash2,
} from 'lucide-react';
import { adminApi } from '../api';
import type { BackupRecord, TenantRecord } from '../types';
import { Badge, Card, Modal, formatBytes, formatTime } from '../components/ui';

export function TenantsManagerPage({
  adminToken,
  onToast,
}: {
  adminToken: string;
  onToast: (msg: string) => void;
}) {
  const [tenants, setTenants] = useState<TenantRecord[]>([]);
  const [backups, setBackups] = useState<BackupRecord[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({
    tenant_id: '',
    display_name: '',
    quota_docs: 1000,
    quota_chunks: 10000,
    rate_limit_rpm: 120,
    description: '',
  });
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = async () => {
    if (!adminToken) return;
    try {
      const [ov, bkp] = await Promise.all([
        adminApi.overview(adminToken),
        adminApi.listBackups(adminToken),
      ]);
      setTenants(ov.tenants);
      setBackups(bkp.backups);
    } catch (err) {
      onToast(`加载租户失败：${(err as Error).message}`);
    }
  };

  useEffect(() => {
    void load();
  }, [adminToken]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await adminApi.createTenant(adminToken, form);
      onToast(`已创建物理隔离租户 ${form.tenant_id}`);
      setCreateOpen(false);
      setForm({
        tenant_id: '',
        display_name: '',
        quota_docs: 1000,
        quota_chunks: 10000,
        rate_limit_rpm: 120,
        description: '',
      });
      await load();
    } catch (err) {
      onToast(`创建失败：${(err as Error).message}`);
    }
  };

  const toggleStatus = async (t: TenantRecord) => {
    const nextStatus = t.status === 'active' ? 'suspended' : 'active';
    setBusyId(t.tenant_id);
    try {
      await adminApi.updateTenant(adminToken, t.tenant_id, { status: nextStatus });
      onToast(nextStatus === 'suspended' ? `租户 ${t.tenant_id} 已熔断停用` : `租户 ${t.tenant_id} 已恢复服务`);
      await load();
    } catch (err) {
      onToast(`更新状态失败：${(err as Error).message}`);
    } finally {
      setBusyId(null);
    }
  };

  const handleBackup = async (tenantId: string) => {
    setBusyId(tenantId);
    try {
      const res = await adminApi.createBackup(adminToken, tenantId);
      onToast(`热备成功：${res.backup.backup_id} (SHA-256 校验通过)`);
      await load();
    } catch (err) {
      onToast(`热备失败：${(err as Error).message}`);
    } finally {
      setBusyId(null);
    }
  };

  const handleRestore = async (b: BackupRecord) => {
    if (!window.confirm(`确认将租户 ${b.tenant_id} 回滚到备份 ${b.backup_id}？`)) return;
    setBusyId(b.backup_id);
    try {
      const res = await adminApi.restoreBackup(adminToken, b.tenant_id, b.backup_id);
      onToast(`物理恢复成功：当前文档数 ${res.stats.docs}`);
      await load();
    } catch (err) {
      onToast(`恢复失败：${(err as Error).message}`);
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (tenantId: string) => {
    if (!window.confirm(`危险操作：物理擦除租户 ${tenantId} 及其全部 SQLite 分库文件？`)) return;
    setBusyId(tenantId);
    try {
      await adminApi.deleteTenant(adminToken, tenantId);
      onToast(`租户 ${tenantId} 已物理擦除`);
      await load();
    } catch (err) {
      onToast(`擦除失败：${(err as Error).message}`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">租户生命周期与物理隔离池</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            每个租户对应独立的 SQLite WAL 分库文件，支持即时流量熔断、在线热备与校验回滚
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCreateOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-blue-500"
        >
          <Plus className="h-4 w-4" /> 新建企业租户
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {tenants.map((t) => (
          <Card key={t.tenant_id} className="flex flex-col justify-between gap-4">
            <div>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{t.display_name}</h3>
                    <Badge variant={t.status === 'active' ? 'emerald' : 'rose'}>
                      {t.status === 'active' ? 'Active' : 'Suspended'}
                    </Badge>
                  </div>
                  <div className="mt-0.5 font-mono text-xs text-slate-500">{t.tenant_id}</div>
                </div>
                <Badge variant="purple">Physical SQLite</Badge>
              </div>
              {t.description && (
                <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">{t.description}</p>
              )}
              <div className="mt-3 grid grid-cols-3 gap-2 rounded-lg border border-slate-200 bg-slate-50 p-2.5 text-xs dark:border-slate-800 dark:bg-slate-950/60">
                <div>
                  <div className="text-[11px] text-slate-500">文档 / 配额</div>
                  <div className="font-semibold tabular-nums">{t.stats.docs} / {t.quota_docs}</div>
                </div>
                <div>
                  <div className="text-[11px] text-slate-500">切片 / 上限</div>
                  <div className="font-semibold tabular-nums">{t.stats.chunks} / {t.quota_chunks}</div>
                </div>
                <div>
                  <div className="text-[11px] text-slate-500">分库大小 · 限流</div>
                  <div className="font-semibold tabular-nums">{formatBytes(t.stats.db_bytes)} · {t.rate_limit_rpm}/m</div>
                </div>
              </div>
              {t.stats.db_path && (
                <div className="mt-2 flex items-center gap-1.5 truncate font-mono text-[11px] text-slate-500">
                  <HardDrive className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{t.stats.db_path}</span>
                </div>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3 dark:border-slate-800">
              <button
                type="button"
                disabled={busyId === t.tenant_id}
                onClick={() => void toggleStatus(t)}
                className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${
                  t.status === 'active'
                    ? 'border-amber-500/40 bg-amber-500/10 text-amber-600 hover:bg-amber-500/20 dark:text-amber-300'
                    : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 hover:bg-emerald-500/20 dark:text-emerald-300'
                }`}
              >
                {t.status === 'active' ? <PauseCircle className="h-3.5 w-3.5" /> : <PlayCircle className="h-3.5 w-3.5" />}
                {t.status === 'active' ? '熔断停用' : '恢复启用'}
              </button>
              <button
                type="button"
                disabled={busyId === t.tenant_id}
                onClick={() => void handleBackup(t.tenant_id)}
                className="inline-flex items-center gap-1 rounded-lg border border-blue-500/40 bg-blue-500/10 px-2.5 py-1.5 text-xs font-medium text-blue-600 hover:bg-blue-500/20 dark:text-blue-300"
              >
                <Archive className="h-3.5 w-3.5" /> 在线热备
              </button>
              <button
                type="button"
                disabled={busyId === t.tenant_id}
                onClick={() => void handleDelete(t.tenant_id)}
                className="ml-auto inline-flex items-center gap-1 rounded-lg border border-rose-500/40 px-2.5 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
              >
                <Trash2 className="h-3.5 w-3.5" /> 物理擦除
              </button>
            </div>
          </Card>
        ))}
      </div>

      {/* Online Hot Backups Table */}
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">在线热备快照仓库（SQLite Backup API + SHA-256）</h3>
          <Badge variant="neutral">{backups.length} 个快照</Badge>
        </div>
        {backups.length === 0 ? (
          <p className="text-xs text-slate-500">暂无备份记录。点击上方租户卡片的「在线热备」生成一致性快照。</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500 dark:border-slate-800">
                  <th className="py-2 pr-4">快照 ID</th>
                  <th className="py-2 pr-4">租户</th>
                  <th className="py-2 pr-4">文档数 / 体积</th>
                  <th className="py-2 pr-4">SHA-256 摘要</th>
                  <th className="py-2 pr-4">校验</th>
                  <th className="py-2 pr-4">创建时间</th>
                  <th className="py-2">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
                {backups.map((b) => (
                  <tr key={b.backup_id}>
                    <td className="py-2.5 pr-4 font-mono">{b.backup_id}</td>
                    <td className="py-2.5 pr-4 font-medium">{b.tenant_id}</td>
                    <td className="py-2.5 pr-4 tabular-nums">{b.doc_count} docs · {formatBytes(b.size_bytes)}</td>
                    <td className="py-2.5 pr-4 font-mono text-[11px] text-slate-500">{b.sha256.slice(0, 18)}…</td>
                    <td className="py-2.5 pr-4">
                      <Badge variant={b.integrity_ok ? 'emerald' : 'rose'}>
                        {b.integrity_ok ? 'integrity_check OK' : '损坏'}
                      </Badge>
                    </td>
                    <td className="py-2.5 pr-4 text-slate-500 tabular-nums">{formatTime(b.created_at)}</td>
                    <td className="py-2.5">
                      <button
                        type="button"
                        disabled={busyId === b.backup_id}
                        onClick={() => void handleRestore(b)}
                        className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-2 py-1 text-[11px] font-medium hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
                      >
                        <RotateCcw className="h-3 w-3" /> 校验并还原
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal open={createOpen} title="新建企业物理隔离租户" onClose={() => setCreateOpen(false)}>
        <form onSubmit={handleCreate} className="space-y-3 text-xs">
          <div>
            <label className="mb-1 block font-medium text-slate-600 dark:text-slate-300">租户 ID（仅限字母数字 . _ -）</label>
            <input
              required
              value={form.tenant_id}
              onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
              placeholder="例如：fin-corp-prod"
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono dark:border-slate-700 dark:bg-slate-950"
            />
          </div>
          <div>
            <label className="mb-1 block font-medium text-slate-600 dark:text-slate-300">显示名称</label>
            <input
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              placeholder="例如：华夏金融生产环境"
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
            />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="mb-1 block font-medium text-slate-600 dark:text-slate-300">文档配额</label>
              <input
                type="number"
                min={1}
                value={form.quota_docs}
                onChange={(e) => setForm({ ...form, quota_docs: Number(e.target.value) })}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
              />
            </div>
            <div>
              <label className="mb-1 block font-medium text-slate-600 dark:text-slate-300">切片配额</label>
              <input
                type="number"
                min={1}
                value={form.quota_chunks}
                onChange={(e) => setForm({ ...form, quota_chunks: Number(e.target.value) })}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
              />
            </div>
            <div>
              <label className="mb-1 block font-medium text-slate-600 dark:text-slate-300">限流 RPM</label>
              <input
                type="number"
                min={1}
                value={form.rate_limit_rpm}
                onChange={(e) => setForm({ ...form, rate_limit_rpm: Number(e.target.value) })}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block font-medium text-slate-600 dark:text-slate-300">业务说明</label>
            <input
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="可选描述"
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setCreateOpen(false)}
              className="rounded-lg border border-slate-300 px-3 py-2 font-medium dark:border-slate-700"
            >
              取消
            </button>
            <button type="submit" className="rounded-lg bg-blue-600 px-4 py-2 font-semibold text-white hover:bg-blue-500">
              创建并初始化分库
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
