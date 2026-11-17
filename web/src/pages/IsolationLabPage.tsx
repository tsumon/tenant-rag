import { useState } from 'react';
import { Lock, Play, ShieldAlert, ShieldCheck } from 'lucide-react';
import { tenantApi } from '../api';
import type { SearchResponse } from '../types';
import { Badge, Card } from '../components/ui';

export function IsolationLabPage({
  demoTenants,
  currentToken,
  onToast,
}: {
  demoTenants: Array<{ tenant_id: string; display_name: string; api_key: string }>;
  currentToken: string;
  onToast: (msg: string) => void;
}) {
  const [keyA, setKeyA] = useState(demoTenants[0]?.api_key || currentToken);
  const [keyB, setKeyB] = useState(demoTenants[1]?.api_key || '');
  const [query, setQuery] = useState('核心高管年终绩效与留任奖金');
  const [rolesA, setRolesA] = useState('');
  const [rolesB, setRolesB] = useState('');
  const [resA, setResA] = useState<SearchResponse | null>(null);
  const [resB, setResB] = useState<SearchResponse | null>(null);
  const [running, setRunning] = useState(false);

  const runComparison = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!keyA.trim()) return onToast('请提供租户 A API Key');
    setRunning(true);
    try {
      const pA = tenantApi.search(keyA.trim(), {
        query,
        k: 4,
        reranker: 'lexical',
        check_abstention: true,
        explain: true,
        principal: {
          user_id: 'lab-user-a',
          roles: rolesA.split(',').map((s) => s.trim()).filter(Boolean),
        },
      });
      const pB = keyB.trim()
        ? tenantApi.search(keyB.trim(), {
            query,
            k: 4,
            reranker: 'lexical',
            check_abstention: true,
            explain: true,
            principal: {
              user_id: 'lab-user-b',
              roles: rolesB.split(',').map((s) => s.trim()).filter(Boolean),
            },
          })
        : Promise.resolve(null);

      const [outA, outB] = await Promise.all([pA, pB]);
      setResA(outA);
      setResB(outB);
    } catch (err) {
      onToast(`对比检索失败：${(err as Error).message}`);
    } finally {
      setRunning(false);
    }
  };

  const renderColumn = (
    title: string,
    keyVal: string,
    setKeyVal: (v: string) => void,
    rolesVal: string,
    setRolesVal: (v: string) => void,
    result: SearchResponse | null,
    accent: string,
  ) => (
    <Card className={`border-t-4 ${accent} space-y-3`}>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">{title}</h3>
        <Badge variant="purple">独立 SQLite 物理分库</Badge>
      </div>
      <div className="space-y-2 text-xs">
        <div>
          <label className="mb-1 block text-slate-500">租户 Bearer Key</label>
          <input
            type="password"
            value={keyVal}
            onChange={(e) => setKeyVal(e.target.value)}
            placeholder="粘贴租户 API Key"
            className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
          />
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className="text-slate-500">模拟角色 (ACL Roles)</label>
            <div className="flex gap-1.5 text-[11px]">
              <button
                type="button"
                onClick={() => setRolesVal('')}
                className="rounded border border-slate-300 px-1.5 py-0.5 hover:border-blue-500 dark:border-slate-700"
              >
                设为普通员工
              </button>
              <button
                type="button"
                onClick={() => setRolesVal('hr_director')}
                className="rounded border border-slate-300 px-1.5 py-0.5 hover:border-blue-500 dark:border-slate-700"
              >
                设为 hr_director
              </button>
            </div>
          </div>
          <input
            value={rolesVal}
            onChange={(e) => setRolesVal(e.target.value)}
            placeholder="例如 hr_director 或留空"
            className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
          />
        </div>
      </div>

      {result && (
        <div className="space-y-2 border-t border-slate-200 pt-3 text-xs dark:border-slate-800">
          <div className="flex items-center justify-between">
            <Badge variant={result.evidence_sufficient ? 'emerald' : 'amber'}>
              {result.evidence_sufficient ? (
                <><ShieldCheck className="h-3 w-3" /> 允许回答（{result.hits.length} 条命中）</>
              ) : (
                <><ShieldAlert className="h-3 w-3" /> 拒答/无可见证据</>
              )}
            </Badge>
            <span className="font-mono text-[11px] text-slate-500">
              ACL 过滤 {result.diagnostics?.filtered_out_acl ?? 0} 条 · {result.latency_ms} ms
            </span>
          </div>

          {!result.evidence_sufficient && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5 text-[11px] text-amber-700 dark:text-amber-300">
              已触发无证据/越权拒答门控：{result.abstention_reason || '未命中授权证据'}
            </div>
          )}

          {result.hits.length === 0 ? (
            <div className="rounded-lg bg-slate-50 p-3 text-slate-500 dark:bg-slate-950/60">
              无匹配或无权限访问的结果（跨租户与受限内容完全不可见）。
            </div>
          ) : (
            result.hits.map((h, i) => (
              <div key={h.doc_id} className="rounded-lg border border-slate-200 bg-slate-50 p-2.5 dark:border-slate-800 dark:bg-slate-950/60">
                <div className="mb-1 flex items-center justify-between font-mono text-[11px]">
                  <span className="font-semibold">
                    #{i + 1} {(h.metadata?._chunk as Record<string, unknown> | undefined)?.parent_id as string || h.doc_id}
                  </span>
                  <span>score {h.score.toFixed(4)}</span>
                </div>
                <p className="line-clamp-3 text-slate-600 dark:text-slate-300">{h.text}</p>
              </div>
            ))
          )}
        </div>
      )}
    </Card>
  );

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">多租户物理隔离与 ACL 演练场</h2>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          对两个不同租户的独立物理 SQLite 分库同时发起相同查询，实时验证零跨租户泄露与文档级权限拦截
        </p>
      </div>

      <Card>
        <form onSubmit={runComparison} className="space-y-3 text-xs">
          <div className="flex flex-wrap gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="输入测试查询语句…"
              className="flex-1 rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
            />
            <button
              type="submit"
              disabled={running}
              className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-500"
            >
              <Play className="h-4 w-4" /> 双租户并发验证
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {[
              '核心高管年终绩效与留任奖金',
              '星辰云科企业版 SLA 赔付标准',
              '青松医疗 CT 设备质保多少个月',
              'DICOM 患者隐私脱敏规范',
            ].map((sample) => (
              <button
                key={sample}
                type="button"
                onClick={() => setQuery(sample)}
                className="inline-flex items-center gap-1 rounded-full border border-slate-300 px-2.5 py-1 text-[11px] hover:border-blue-500 dark:border-slate-700"
              >
                <Lock className="h-3 w-3 text-amber-500" /> {sample}
              </button>
            ))}
          </div>
        </form>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {renderColumn(
          '租户 A · 星辰云科 (Aurora Cloud)',
          keyA,
          setKeyA,
          rolesA,
          setRolesA,
          resA,
          'border-t-blue-500',
        )}
        {renderColumn(
          '租户 B · 青松医疗 (Cedar MedTech)',
          keyB,
          setKeyB,
          rolesB,
          setRolesB,
          resB,
          'border-t-emerald-500',
        )}
      </div>
    </div>
  );
}
