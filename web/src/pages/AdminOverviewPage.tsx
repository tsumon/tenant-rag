import { useEffect, useState } from 'react';
import {
  Activity,
  Database,
  FileCode2,
  HardDrive,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Timer,
  Users,
} from 'lucide-react';
import { adminApi, fetchMetricsText } from '../api';
import type { AdminOverview } from '../types';
import { Badge, Card, StatTile, formatBytes } from '../components/ui';

export function AdminOverviewPage({
  adminToken,
  onToast,
}: {
  adminToken: string;
  onToast: (msg: string) => void;
}) {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [metricsText, setMetricsText] = useState('');
  const [showMetrics, setShowMetrics] = useState(false);

  const load = async () => {
    if (!adminToken) return;
    setLoading(true);
    try {
      const [ov, prom] = await Promise.all([
        adminApi.overview(adminToken),
        fetchMetricsText().catch(() => ''),
      ]);
      setData(ov);
      setMetricsText(prom);
    } catch (err) {
      onToast(`加载大盘失败：${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [adminToken]);

  if (!data) {
    return (
      <Card>
        <div className="flex items-center justify-between">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {loading ? '正在加载平台运行态势…' : '请先连接平台超管 Token 或启用 Demo 模式。'}
          </p>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500"
          >
            <RefreshCw className="h-3.5 w-3.5" /> 刷新
          </button>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">平台全局运行态势</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            实时聚合各物理分库租户容量、检索延时分布与网关限流 / 鉴权拒止计数
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowMetrics((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          >
            <FileCode2 className="h-3.5 w-3.5" />
            {showMetrics ? '隐藏 Prometheus /metrics' : '查看 Prometheus /metrics'}
          </button>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新数据
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="活跃租户 / 总租户"
          value={`${data.totals.active_tenants} / ${data.totals.tenants}`}
          sub={`物理隔离 SQLite WAL · ${data.totals.backups} 份热备`}
          icon={<Users className="h-5 w-5" />}
          accent="blue"
        />
        <StatTile
          label="全平台文档 / 切片"
          value={`${data.totals.docs} / ${data.totals.chunks}`}
          sub={`物理存储占用 ${formatBytes(data.totals.storage_bytes)}`}
          icon={<Database className="h-5 w-5" />}
          accent="emerald"
        />
        <StatTile
          label="HTTP 总请求 / P95 延迟"
          value={`${data.metrics.total_requests} · ${data.metrics.latency_p95_ms} ms`}
          sub={`P50 ${data.metrics.latency_p50_ms} ms · 检索 ${data.metrics.total_searches} 次`}
          icon={<Timer className="h-5 w-5" />}
          accent="purple"
        />
        <StatTile
          label="拒答门控 / 鉴权拒止"
          value={`${data.metrics.total_abstentions} / ${data.metrics.auth_denied_total}`}
          sub={`限流拒绝 (429): ${data.metrics.rate_limited_total} 次`}
          icon={<ShieldAlert className="h-5 w-5" />}
          accent="amber"
        />
      </div>

      {/* Tenant capacity overview table */}
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <HardDrive className="h-4 w-4 text-blue-500" />
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">各租户物理分库配额与健康度</h3>
          </div>
          <Badge variant="blue">
            Embedding: {String(data.embedder?.provider || 'tenant-rag')} / {String(data.embedder?.model || 'lexical-hash')}
          </Badge>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 text-slate-500 dark:border-slate-800 dark:text-slate-400">
                <th className="py-2.5 pr-4 font-medium">租户 ID / 名称</th>
                <th className="py-2.5 pr-4 font-medium">状态</th>
                <th className="py-2.5 pr-4 font-medium">文档配额使用率</th>
                <th className="py-2.5 pr-4 font-medium">切片数</th>
                <th className="py-2.5 pr-4 font-medium">物理分库体积</th>
                <th className="py-2.5 pr-4 font-medium">限流策略</th>
                <th className="py-2.5 font-medium">模型维度绑定</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
              {data.tenants.map((t) => {
                const pct = t.quota_docs > 0 ? Math.min(100, Math.round((t.stats.docs / t.quota_docs) * 100)) : 0;
                return (
                  <tr key={t.tenant_id} className="hover:bg-slate-50/70 dark:hover:bg-slate-800/30">
                    <td className="py-3 pr-4">
                      <div className="font-semibold text-slate-900 dark:text-slate-100">{t.display_name}</div>
                      <div className="font-mono text-[11px] text-slate-500">{t.tenant_id}</div>
                    </td>
                    <td className="py-3 pr-4">
                      <Badge variant={t.status === 'active' ? 'emerald' : 'rose'}>
                        {t.status === 'active' ? (
                          <><ShieldCheck className="h-3 w-3" /> 运行中</>
                        ) : (
                          '已熔断'
                        )}
                      </Badge>
                    </td>
                    <td className="py-3 pr-4 min-w-[160px]">
                      <div className="flex items-center justify-between tabular-nums">
                        <span>{t.stats.docs} / {t.quota_docs}</span>
                        <span className="text-slate-500">{pct}%</span>
                      </div>
                      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
                        <div className="h-full rounded-full bg-blue-500" style={{ width: `${pct}%` }} />
                      </div>
                    </td>
                    <td className="py-3 pr-4 tabular-nums">{t.stats.chunks}</td>
                    <td className="py-3 pr-4 font-mono tabular-nums">{formatBytes(t.stats.db_bytes)}</td>
                    <td className="py-3 pr-4 tabular-nums">{t.rate_limit_rpm} RPM</td>
                    <td className="py-3 font-mono text-[11px] text-slate-500 dark:text-slate-400">
                      {t.stats.model_identity
                        ? `${t.stats.model_identity.model} (${t.stats.model_identity.dimension}d)`
                        : '未绑定'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {showMetrics && (
        <Card>
          <div className="mb-2 flex items-center gap-2">
            <Activity className="h-4 w-4 text-emerald-500" />
            <h3 className="text-sm font-semibold">Prometheus `/metrics` 原始暴露指标</h3>
          </div>
          <pre className="max-h-72 overflow-auto rounded-lg bg-slate-950 p-3 text-[11px] leading-relaxed text-emerald-300">
            {metricsText || '# 暂无指标输出'}
          </pre>
        </Card>
      )}
    </div>
  );
}
