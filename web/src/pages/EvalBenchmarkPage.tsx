import { useState } from 'react';
import { BarChart3, Loader2, Play } from 'lucide-react';
import { tenantApi } from '../api';
import type { EvalRunResponse } from '../types';
import { Badge, Card } from '../components/ui';

export function EvalBenchmarkPage({
  token,
  onToast,
}: {
  token: string;
  onToast: (msg: string) => void;
}) {
  const [suite, setSuite] = useState<'golden' | 'pipeline' | 'semantic' | 'tenant_live'>('golden');
  const [k, setK] = useState(3);
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<EvalRunResponse | null>(null);

  const run = async () => {
    setLoading(true);
    try {
      const res = await tenantApi.runEval(token, { suite, k });
      setReport(res);
      onToast(`评测完成：${res.query_count} 道题目`);
    } catch (err) {
      onToast(`评测运行失败：${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">在线检索策略对比评测台</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            实时对比 Vector、FTS5 Trigram、Hybrid RRF、Hybrid Score 与 Hybrid + LexicalCrossReranker 的 Recall@k 与 MRR@k
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <select
            value={suite}
            onChange={(e) => setSuite(e.target.value as typeof suite)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 dark:border-slate-700 dark:bg-slate-900"
          >
            <option value="golden">Golden 全部评测集 (30题)</option>
            <option value="pipeline">Pipeline 管线回归集 (16题)</option>
            <option value="semantic">Semantic 双语挑战集 (14题)</option>
            <option value="tenant_live">当前租户实时语料探针</option>
          </select>
          <select
            value={k}
            onChange={(e) => setK(Number(e.target.value))}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 dark:border-slate-700 dark:bg-slate-900"
          >
            <option value={3}>@K = 3</option>
            <option value={5}>@K = 5</option>
          </select>
          <button
            type="button"
            disabled={loading}
            onClick={() => void run()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-1.5 font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            运行基准评测
          </button>
        </div>
      </div>

      {!report ? (
        <Card className="py-12 text-center text-xs text-slate-500">
          <BarChart3 className="mx-auto mb-2 h-8 w-8 text-blue-500/60" />
          选择测试集与截断 K 值，点击「运行基准评测」查看 5 种检索融合与重排策略的定量对比结果。
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {Object.entries(report.summary).map(([id, m]) => (
              <Card key={id} className="p-4">
                <div className="mb-1 text-xs font-semibold text-slate-700 dark:text-slate-200">{m.label}</div>
                <div className="text-[11px] font-mono text-slate-500">{m.avg_latency_ms} ms/query</div>
                <div className="mt-3 space-y-2 text-xs">
                  <div>
                    <div className="flex justify-between tabular-nums">
                      <span>Recall@{report.k}</span>
                      <span className="font-bold text-blue-500">{(m.recall_at_k * 100).toFixed(1)}%</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
                      <div className="h-full bg-blue-500" style={{ width: `${m.recall_at_k * 100}%` }} />
                    </div>
                  </div>
                  <div>
                    <div className="flex justify-between tabular-nums">
                      <span>MRR@{report.k}</span>
                      <span className="font-bold text-emerald-500">{(m.mrr_at_k * 100).toFixed(1)}%</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
                      <div className="h-full bg-emerald-500" style={{ width: `${m.mrr_at_k * 100}%` }} />
                    </div>
                  </div>
                </div>
              </Card>
            ))}
          </div>

          <Card>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">逐查询命中详情（{report.dataset}）</h3>
              <Badge variant="blue">{report.query_count} queries</Badge>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-200 text-slate-500 dark:border-slate-800">
                    <th className="py-2 pr-3">ID</th>
                    <th className="py-2 pr-3">查询问题</th>
                    <th className="py-2 pr-3">期望文档</th>
                    <th className="py-2 pr-3">Vector</th>
                    <th className="py-2 pr-3">Hybrid RRF</th>
                    <th className="py-2">Hybrid + Rerank</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
                  {report.queries.map((q) => (
                    <tr key={q.id}>
                      <td className="py-2 pr-3 font-mono text-slate-500">{q.id}</td>
                      <td className="py-2 pr-3 max-w-xs truncate">{q.query}</td>
                      <td className="py-2 pr-3 font-mono text-[11px] text-slate-500">{q.expected.join(', ')}</td>
                      {(['vector', 'hybrid_rrf', 'hybrid_rerank'] as const).map((modeKey) => {
                        const cell = q.results[modeKey];
                        return (
                          <td key={modeKey} className="py-2 pr-3">
                            <Badge variant={cell?.recall === 1 ? 'emerald' : cell?.recall > 0 ? 'amber' : 'rose'}>
                              R={cell?.recall ?? 0} · MRR={cell?.mrr ?? 0}
                            </Badge>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
