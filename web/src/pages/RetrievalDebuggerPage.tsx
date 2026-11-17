import { useState } from 'react';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Eye,
  Filter,
  Play,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { tenantApi } from '../api';
import type { DocumentDetail, SearchResponse } from '../types';
import { ChunkSourceViewer } from '../components/ChunkSourceViewer';
import { Badge, Card, Modal } from '../components/ui';

interface FilterRow {
  field: string;
  op: string;
  value: string;
}

export function RetrievalDebuggerPage({
  token,
  onToast,
}: {
  token: string;
  onToast: (msg: string) => void;
}) {
  const [query, setQuery] = useState('星辰云科企业版 SLA 可用性赔付');
  const [k, setK] = useState(5);
  const [fusion, setFusion] = useState<'rrf' | 'score'>('rrf');
  const [lexicalWeight, setLexicalWeight] = useState(0.2);
  const [reranker, setReranker] = useState<'lexical' | 'none'>('lexical');
  const [contextWindow, setContextWindow] = useState(1);
  const [checkAbstention, setCheckAbstention] = useState(true);
  const [minVectorScore, setMinVectorScore] = useState(0.28);

  const [filterRows, setFilterRows] = useState<FilterRow[]>([]);
  const [userId, setUserId] = useState('emp-102');
  const [rolesInput, setRolesInput] = useState('');
  const [isAdminPrincipal, setIsAdminPrincipal] = useState(false);

  const [loading, setLoading] = useState(false);
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [inspector, setInspector] = useState<{
    detail: DocumentDetail;
    range: { start: number; end: number };
  } | null>(null);

  const buildFilters = (): Record<string, unknown> | undefined => {
    if (!filterRows.length) return undefined;
    const out: Record<string, unknown> = {};
    for (const row of filterRows) {
      if (!row.field.trim()) continue;
      let val: unknown = row.value;
      if (row.op === '$in' || row.op === '$nin') {
        val = row.value
          .split(',')
          .map((v) => v.trim())
          .filter(Boolean);
      } else if (row.op === '$exists') {
        val = row.value.toLowerCase() !== 'false';
      } else if (/^-?\d+(\.\d+)?$/.test(row.value.trim())) {
        val = Number(row.value.trim());
      }
      out[row.field.trim()] = { [row.op]: val };
    }
    return Object.keys(out).length ? out : undefined;
  };

  const runSearch = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!query.trim()) return onToast('请输入检索查询语句');
    setLoading(true);
    try {
      const payload: Record<string, unknown> = {
        query: query.trim(),
        k,
        fusion,
        reranker,
        context_window: contextWindow,
        check_abstention: checkAbstention,
        min_vector_score: minVectorScore,
        explain: true,
        principal: {
          user_id: userId.trim() || null,
          roles: rolesInput
            .split(',')
            .map((r) => r.trim())
            .filter(Boolean),
          is_admin: isAdminPrincipal,
        },
      };
      if (fusion === 'score') payload.lexical_weight = lexicalWeight;
      const builtFilters = buildFilters();
      if (builtFilters) payload.filters = builtFilters;

      const data = await tenantApi.search(token, payload);
      setRes(data);
    } catch (err) {
      onToast(`检索失败：${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const openSourceOffset = async (parentId: string, start: number, end: number) => {
    try {
      const detail = await tenantApi.getDoc(token, parentId);
      setInspector({ detail, range: { start, end } });
    } catch (err) {
      onToast(`打开原文失败：${(err as Error).message}`);
    }
  };

  const timings = res?.diagnostics?.timings_ms || {};
  const totalTime = timings.total || res?.latency_ms || 1;

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">企业混合检索可视化调试器</h2>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          可视化调试 Metadata 预过滤、ACL 角色权限、双路融合、Cross-Lexical 二阶段重排、父子上下文窗口及各阶段延迟瀑布
        </p>
      </div>

      <Card>
        <form onSubmit={runSearch} className="space-y-4 text-xs">
          <div className="flex flex-wrap gap-2">
            <div className="relative min-w-[260px] flex-1">
              <Search className="pointer-events-none absolute top-2.5 left-3 h-4 w-4 text-slate-400" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="输入检索问题或关键词…"
                className="w-full rounded-xl border border-slate-300 bg-white py-2 pr-3 pl-9 text-sm dark:border-slate-700 dark:bg-slate-950"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
            >
              <Play className="h-4 w-4" /> 执行检索调试
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <div>
              <label className="mb-1 block text-slate-500">Top K</label>
              <select
                value={k}
                onChange={(e) => setK(Number(e.target.value))}
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-950"
              >
                {[3, 5, 10, 20].map((v) => (
                  <option key={v} value={v}>Top {v}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-slate-500">融合策略</label>
              <select
                value={fusion}
                onChange={(e) => setFusion(e.target.value as 'rrf' | 'score')}
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-950"
              >
                <option value="rrf">RRF 排名融合</option>
                <option value="score">Score 分差融合</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-slate-500">
                {fusion === 'score' ? `词法权重 (${lexicalWeight})` : '二阶段重排'}
              </label>
              {fusion === 'score' ? (
                <input
                  type="number"
                  step={0.1}
                  min={0}
                  max={1}
                  value={lexicalWeight}
                  onChange={(e) => setLexicalWeight(Number(e.target.value))}
                  className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-950"
                />
              ) : (
                <select
                  value={reranker}
                  onChange={(e) => setReranker(e.target.value as 'lexical' | 'none')}
                  className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-950"
                >
                  <option value="lexical">LexicalCrossReranker</option>
                  <option value="none">关闭重排 (none)</option>
                </select>
              )}
            </div>
            <div>
              <label className="mb-1 block text-slate-500">父子上下文窗口</label>
              <select
                value={contextWindow}
                onChange={(e) => setContextWindow(Number(e.target.value))}
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-950"
              >
                <option value={0}>0（仅命中切片）</option>
                <option value={1}>±1 相邻切片</option>
                <option value={2}>±2 相邻切片</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-slate-500">拒答向量阈值</label>
              <input
                type="number"
                step={0.01}
                min={0}
                max={1}
                value={minVectorScore}
                onChange={(e) => setMinVectorScore(Number(e.target.value))}
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-950"
              />
            </div>
            <div className="flex items-end pb-1">
              <label className="inline-flex items-center gap-1.5 text-slate-600 dark:text-slate-300">
                <input
                  type="checkbox"
                  checked={checkAbstention}
                  onChange={(e) => setCheckAbstention(e.target.checked)}
                />
                开启证据拒答门控
              </label>
            </div>
          </div>

          {/* Pre-Filter & ACL Simulator Row */}
          <div className="grid grid-cols-1 gap-4 border-t border-slate-200 pt-3 dark:border-slate-800 lg:grid-cols-2">
            <div>
              <div className="mb-2 flex items-center justify-between">
                <span className="inline-flex items-center gap-1 font-medium text-slate-600 dark:text-slate-300">
                  <Filter className="h-3.5 w-3.5 text-blue-500" /> Metadata 预过滤 DSL
                </span>
                <button
                  type="button"
                  onClick={() => setFilterRows([...filterRows, { field: 'dept', op: '$eq', value: 'cloud' }])}
                  className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-2 py-1 text-[11px] dark:border-slate-700"
                >
                  <Plus className="h-3 w-3" /> 添加条件
                </button>
              </div>
              <div className="space-y-1.5">
                {filterRows.length === 0 ? (
                  <p className="text-[11px] text-slate-400">未设置预过滤条件（扫描当前租户全部授权文档）</p>
                ) : (
                  filterRows.map((row, idx) => (
                    <div key={idx} className="flex items-center gap-1.5">
                      <input
                        value={row.field}
                        onChange={(e) => {
                          const next = [...filterRows];
                          next[idx].field = e.target.value;
                          setFilterRows(next);
                        }}
                        placeholder="字段如 sla_pct"
                        className="w-28 rounded-md border border-slate-300 bg-white px-2 py-1 font-mono dark:border-slate-700 dark:bg-slate-950"
                      />
                      <select
                        value={row.op}
                        onChange={(e) => {
                          const next = [...filterRows];
                          next[idx].op = e.target.value;
                          setFilterRows(next);
                        }}
                        className="rounded-md border border-slate-300 bg-white px-2 py-1 font-mono dark:border-slate-700 dark:bg-slate-950"
                      >
                        {['$eq', '$ne', '$gte', '$gt', '$lte', '$lt', '$in', '$contains', '$exists'].map((op) => (
                          <option key={op} value={op}>{op}</option>
                        ))}
                      </select>
                      <input
                        value={row.value}
                        onChange={(e) => {
                          const next = [...filterRows];
                          next[idx].value = e.target.value;
                          setFilterRows(next);
                        }}
                        placeholder="值"
                        className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-2 py-1 font-mono dark:border-slate-700 dark:bg-slate-950"
                      />
                      <button
                        type="button"
                        onClick={() => setFilterRows(filterRows.filter((_, i) => i !== idx))}
                        className="p-1 text-rose-500 hover:text-rose-400"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div>
              <div className="mb-2 flex items-center justify-between">
                <span className="inline-flex items-center gap-1 font-medium text-slate-600 dark:text-slate-300">
                  <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" /> 查询身份模拟 (ACL Principal)
                </span>
                <label className="inline-flex items-center gap-1 text-[11px] text-slate-500">
                  <input
                    type="checkbox"
                    checked={isAdminPrincipal}
                    onChange={(e) => setIsAdminPrincipal(e.target.checked)}
                  />
                  is_admin 超级豁免
                </label>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <input
                  value={userId}
                  onChange={(e) => setUserId(e.target.value)}
                  placeholder="user_id（如 ceo-alice）"
                  className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
                />
                <input
                  value={rolesInput}
                  onChange={(e) => setRolesInput(e.target.value)}
                  placeholder="roles 逗号分隔（如 hr_director）"
                  className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
                />
              </div>
            </div>
          </div>
        </form>
      </Card>

      {res && (
        <>
          {/* Abstention & Diagnostics Banner */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
            <Card className="lg:col-span-7 p-4">
              <div className="mb-2.5 flex items-center justify-between">
                <span className="text-xs font-semibold">流水线分阶段耗时瀑布（总计 {res.latency_ms} ms）</span>
                <Badge variant={res.evidence_sufficient ? 'emerald' : 'amber'}>
                  {res.evidence_sufficient ? '证据充分通过' : '触发拒答门控'}
                </Badge>
              </div>
              <div className="space-y-1.5 text-[11px]">
                {[
                  ['embed', 'Embedding 编码', 'bg-indigo-500'],
                  ['filter_acl', 'ACL + Metadata 预过滤', 'bg-amber-500'],
                  ['vector', '物理分库向量召回', 'bg-blue-500'],
                  ['fts', 'FTS5 Trigram 全文', 'bg-emerald-500'],
                  ['fusion', '双路融合排名', 'bg-purple-500'],
                  ['rerank', '二阶段 Reranker', 'bg-pink-500'],
                  ['context_expand', '父子上下文展开', 'bg-cyan-500'],
                ].map(([key, label, color]) => {
                  const val = timings[key] ?? 0;
                  const pct = Math.max(2, Math.min(100, (val / Math.max(0.1, totalTime)) * 100));
                  return (
                    <div key={key} className="grid grid-cols-12 items-center gap-2">
                      <span className="col-span-4 truncate text-slate-600 dark:text-slate-400">{label}</span>
                      <div className="col-span-6 h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
                        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
                      </div>
                      <span className="col-span-2 text-right font-mono tabular-nums text-slate-500">
                        {val.toFixed(2)} ms
                      </span>
                    </div>
                  );
                })}
              </div>
            </Card>

            <Card className="lg:col-span-5 p-4 text-xs space-y-2">
              <div className="font-semibold">候选集过滤与 FTS5 Trigram 拆词</div>
              <div className="grid grid-cols-2 gap-2 rounded-lg bg-slate-50 p-2.5 dark:bg-slate-950/60">
                <div>总切片数：<span className="font-semibold tabular-nums">{res.diagnostics?.corpus_chunks ?? '-'}</span></div>
                <div>合法候选：<span className="font-semibold tabular-nums text-emerald-500">{res.diagnostics?.allowed_candidates ?? '-'}</span></div>
                <div>ACL 拦截：<span className="font-semibold tabular-nums text-amber-500">{res.diagnostics?.filtered_out_acl ?? 0}</span></div>
                <div>Metadata 拦截：<span className="font-semibold tabular-nums text-blue-500">{res.diagnostics?.filtered_out_metadata ?? 0}</span></div>
              </div>
              <div>
                <div className="mb-1 text-[11px] text-slate-500">安全生成的 FTS5 MATCH 表达式：</div>
                <div className="max-h-16 overflow-auto rounded-md bg-slate-950 p-2 font-mono text-[11px] text-amber-300">
                  {res.diagnostics?.fts_query || '(无有效 >=3 字 trigram 或字母数字词，由向量与 CrossRerank 兜底)'}
                </div>
              </div>
            </Card>
          </div>

          {!res.evidence_sufficient && (
            <div className="flex items-center gap-2.5 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3.5 text-xs text-amber-700 dark:text-amber-300">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span>
                <strong>Abstention Gate 拦截：</strong> {res.abstention_reason}
              </span>
            </div>
          )}

          {/* Ranked Hits */}
          <div className="space-y-3">
            {res.hits.map((h, i) => {
              const chunkMeta = (h.metadata?._chunk as Record<string, unknown> | undefined) ?? undefined;
              const parentId = (chunkMeta?.parent_id as string) || h.doc_id;
              const start = h.expanded_start ?? (chunkMeta?.start as number) ?? 0;
              const end = h.expanded_end ?? (chunkMeta?.end as number) ?? h.text.length;
              const delta = h.rank_delta ?? 0;

              return (
                <Card key={h.doc_id} className="border-l-4 border-l-blue-500 p-4">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm font-bold text-blue-500">#{i + 1}</span>
                      <span className="font-mono text-xs font-semibold">{parentId}</span>
                      {h.matched.includes('vector') && <Badge variant="blue">向量</Badge>}
                      {h.matched.includes('fts') && <Badge variant="emerald">FTS5 关键词</Badge>}
                      {delta !== 0 && (
                        <Badge variant={delta > 0 ? 'purple' : 'amber'}>
                          {delta > 0 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />}
                          Rerank {delta > 0 ? `+${delta}` : delta}
                        </Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-2 text-[11px] font-mono tabular-nums text-slate-500">
                      <span>final={h.score.toFixed(4)}</span>
                      {h.vector_score !== null && h.vector_score !== undefined && (
                        <span>cos={h.vector_score.toFixed(3)}</span>
                      )}
                      {h.cross_score !== undefined && (
                        <span className="inline-flex items-center gap-0.5 text-purple-500">
                          <Sparkles className="h-3 w-3" /> cross={h.cross_score.toFixed(3)}
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={() => void openSourceOffset(parentId, start, end)}
                        className="ml-2 inline-flex items-center gap-1 rounded-md border border-blue-500/40 bg-blue-500/10 px-2 py-1 text-blue-600 hover:bg-blue-500/20 dark:text-blue-300"
                      >
                        <Eye className="h-3 w-3" /> 透视原文 [{start},{end})
                      </button>
                    </div>
                  </div>
                  <div className="rounded-lg bg-slate-50 p-3 text-xs leading-relaxed whitespace-pre-wrap text-slate-700 dark:bg-slate-950/60 dark:text-slate-200">
                    {h.expanded_text || h.text}
                  </div>
                </Card>
              );
            })}
          </div>
        </>
      )}

      <Modal
        open={Boolean(inspector)}
        title={inspector ? `原文证据定位 · ${inspector.detail.doc_id}` : ''}
        onClose={() => setInspector(null)}
        maxWidth="max-w-5xl"
      >
        {inspector && (
          <ChunkSourceViewer
            detail={inspector.detail}
            highlightRange={inspector.range}
            onSelectRange={(start, end) => setInspector({ ...inspector, range: { start, end } })}
          />
        )}
      </Modal>
    </div>
  );
}
