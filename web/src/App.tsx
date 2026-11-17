import { useEffect, useState } from 'react';
import {
  Activity,
  BarChart3,
  BookOpenCheck,
  Building2,
  Database,
  FileSearch,
  KeyRound,
  Moon,
  Shield,
  ShieldCheck,
  Sparkles,
  Sun,
  Users,
} from 'lucide-react';
import { fetchDemoBootstrap, fetchHealth, tenantApi } from './api';
import type { AdminTab, DemoBootstrapData, Perspective, TenantTab } from './types';
import { Badge, Card } from './components/ui';
import { AdminOverviewPage } from './pages/AdminOverviewPage';
import { TenantsManagerPage } from './pages/TenantsManagerPage';
import { ApiKeysManagerPage } from './pages/ApiKeysManagerPage';
import { AuditLogsPage } from './pages/AuditLogsPage';
import { KnowledgeBasePage } from './pages/KnowledgeBasePage';
import { RetrievalDebuggerPage } from './pages/RetrievalDebuggerPage';
import { GroundedChatPage } from './pages/GroundedChatPage';
import { EvalBenchmarkPage } from './pages/EvalBenchmarkPage';
import { IsolationLabPage } from './pages/IsolationLabPage';

export function App() {
  const [dark, setDark] = useState(true);
  const [perspective, setPerspective] = useState<Perspective>('admin');
  const [adminTab, setAdminTab] = useState<AdminTab>('overview');
  const [tenantTab, setTenantTab] = useState<TenantTab>('kb');

  const [demoData, setDemoData] = useState<DemoBootstrapData | null>(null);
  const [adminToken, setAdminToken] = useState('');
  const [tenantToken, setTenantToken] = useState('');
  const [tenantInfo, setTenantInfo] = useState<{
    tenant_id: string;
    display_name: string;
    key_prefix: string;
    scopes: string[];
  } | null>(null);

  const [manualAdminInput, setManualAdminInput] = useState('');
  const [manualKeyInput, setManualKeyInput] = useState('');
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast((cur) => (cur === msg ? null : cur)), 3200);
  };

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
  }, [dark]);

  useEffect(() => {
    const init = async () => {
      try {
        const health = await fetchHealth();
        if (health.demo_mode) {
          const boot = await fetchDemoBootstrap();
          setDemoData(boot);
          setAdminToken(boot.admin_token);
          if (boot.tenants.length > 0) {
            await connectTenantKey(boot.tenants[0].api_key, true);
          }
        }
      } catch {
        // Non-demo mode: user supplies token manually
      }
    };
    void init();
  }, []);

  const connectTenantKey = async (key: string, silent = false) => {
    try {
      const info = await tenantApi.whoami(key);
      setTenantToken(key);
      setTenantInfo({
        tenant_id: info.tenant_id,
        display_name: info.display_name,
        key_prefix: info.key_prefix,
        scopes: info.scopes,
      });
      if (!silent) showToast(`已接入租户工作台：${info.display_name}`);
    } catch (err) {
      if (!silent) showToast(`租户连接失败：${(err as Error).message}`);
    }
  };

  const adminNav: Array<{ id: AdminTab; label: string; icon: React.ReactNode }> = [
    { id: 'overview', label: '全局运行大盘', icon: <Activity className="h-4 w-4" /> },
    { id: 'tenants', label: '租户与物理隔离', icon: <Users className="h-4 w-4" /> },
    { id: 'keys', label: 'API Key 生命周期', icon: <KeyRound className="h-4 w-4" /> },
    { id: 'audit', label: '防篡改审计中心', icon: <ShieldCheck className="h-4 w-4" /> },
  ];

  const tenantNav: Array<{ id: TenantTab; label: string; icon: React.ReactNode }> = [
    { id: 'kb', label: '知识库与切片透视', icon: <Database className="h-4 w-4" /> },
    { id: 'search', label: '混合检索调试器', icon: <FileSearch className="h-4 w-4" /> },
    { id: 'chat', label: '引用溯源问答台', icon: <BookOpenCheck className="h-4 w-4" /> },
    { id: 'eval', label: '在线检索评测台', icon: <BarChart3 className="h-4 w-4" /> },
    { id: 'isolation', label: '多租户隔离演练场', icon: <Shield className="h-4 w-4" /> },
  ];

  return (
    <div className="min-h-screen bg-slate-100 text-slate-900 transition-colors dark:bg-slate-950 dark:text-slate-100">
      <div className="flex min-h-screen flex-col lg:flex-row">
        {/* Sidebar */}
        <aside className="w-full shrink-0 border-b border-slate-200 bg-white p-4 dark:border-slate-800/90 dark:bg-slate-900/90 lg:w-68 lg:border-r lg:border-b-0">
          <div className="mb-5 flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-600 text-white shadow-sm">
                <Sparkles className="h-5 w-5" />
              </div>
              <div>
                <div className="text-sm font-bold tracking-tight">Tenant RAG</div>
                <div className="text-[11px] text-slate-500 dark:text-slate-400">Enterprise v0.6.0</div>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setDark((d) => !d)}
              className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
              title="切换浅色/深色主题"
            >
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>

          {/* Dual Perspective Switcher */}
          <div className="mb-4 grid grid-cols-2 gap-1 rounded-xl border border-slate-200 bg-slate-100 p-1 text-xs dark:border-slate-800 dark:bg-slate-950">
            <button
              type="button"
              onClick={() => setPerspective('admin')}
              className={`flex items-center justify-center gap-1 rounded-lg py-2 font-semibold transition ${
                perspective === 'admin'
                  ? 'bg-white text-blue-600 shadow-xs dark:bg-slate-800 dark:text-blue-300'
                  : 'text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
              }`}
            >
              <Shield className="h-3.5 w-3.5" /> 平台超管
            </button>
            <button
              type="button"
              onClick={() => setPerspective('tenant')}
              className={`flex items-center justify-center gap-1 rounded-lg py-2 font-semibold transition ${
                perspective === 'tenant'
                  ? 'bg-white text-blue-600 shadow-xs dark:bg-slate-800 dark:text-blue-300'
                  : 'text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'
              }`}
            >
              <Building2 className="h-3.5 w-3.5" /> 租户工作台
            </button>
          </div>

          {/* Navigation Menu */}
          <nav className="space-y-1">
            {perspective === 'admin'
              ? adminNav.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setAdminTab(item.id)}
                    className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-xs font-medium transition ${
                      adminTab === item.id
                        ? 'bg-blue-600 text-white shadow-xs'
                        : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800/70'
                    }`}
                  >
                    {item.icon}
                    {item.label}
                  </button>
                ))
              : tenantNav.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setTenantTab(item.id)}
                    className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-xs font-medium transition ${
                      tenantTab === item.id
                        ? 'bg-blue-600 text-white shadow-xs'
                        : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800/70'
                    }`}
                  >
                    {item.icon}
                    {item.label}
                  </button>
                ))}
          </nav>

          {/* Identity / Quick Switcher Card */}
          <div className="mt-6 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs dark:border-slate-800 dark:bg-slate-950/70">
            {perspective === 'admin' ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-slate-600 dark:text-slate-300">超管控制面凭证</span>
                  <Badge variant={adminToken ? 'emerald' : 'amber'}>
                    {adminToken ? '已鉴权' : '未连接'}
                  </Badge>
                </div>
                <div className="flex gap-1.5">
                  <input
                    type="password"
                    value={manualAdminInput}
                    onChange={(e) => setManualAdminInput(e.target.value)}
                    placeholder="输入 X-Admin-Token"
                    className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1 font-mono text-[11px] dark:border-slate-700 dark:bg-slate-900"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      if (manualAdminInput.trim()) {
                        setAdminToken(manualAdminInput.trim());
                        showToast('已更新超管 Token');
                      }
                    }}
                    className="rounded-lg bg-blue-600 px-2.5 py-1 text-[11px] font-semibold text-white"
                  >
                    连接
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-2.5">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-slate-600 dark:text-slate-300">当前租户上下文</span>
                  <Badge variant={tenantInfo ? 'emerald' : 'amber'}>
                    {tenantInfo ? tenantInfo.tenant_id : '未连接'}
                  </Badge>
                </div>
                {tenantInfo && (
                  <div className="space-y-1 text-[11px] text-slate-500">
                    <div className="font-medium text-slate-800 dark:text-slate-200">{tenantInfo.display_name}</div>
                    <div className="font-mono">Key: {tenantInfo.key_prefix}</div>
                    <div className="flex flex-wrap gap-1 pt-0.5">
                      {tenantInfo.scopes.map((s) => (
                        <Badge key={s} variant="blue">{s}</Badge>
                      ))}
                    </div>
                  </div>
                )}

                {demoData && demoData.tenants.length > 0 && (
                  <div className="space-y-1 border-t border-slate-200 pt-2 dark:border-slate-800">
                    <div className="text-[11px] text-slate-500">Demo 一键切换租户：</div>
                    {demoData.tenants.map((dt) => (
                      <button
                        key={dt.tenant_id}
                        type="button"
                        onClick={() => void connectTenantKey(dt.api_key)}
                        className={`flex w-full items-center justify-between rounded-lg border px-2 py-1.5 text-[11px] transition ${
                          tenantInfo?.tenant_id === dt.tenant_id
                            ? 'border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-300'
                            : 'border-slate-200 hover:border-slate-300 dark:border-slate-800'
                        }`}
                      >
                        <span className="truncate font-medium">{dt.display_name}</span>
                        <span className="font-mono opacity-75">{dt.docs}d</span>
                      </button>
                    ))}
                  </div>
                )}

                <div className="flex gap-1.5 pt-1">
                  <input
                    type="password"
                    value={manualKeyInput}
                    onChange={(e) => setManualKeyInput(e.target.value)}
                    placeholder="或粘贴 tr_live_xxx"
                    className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1 font-mono text-[11px] dark:border-slate-700 dark:bg-slate-900"
                  />
                  <button
                    type="button"
                    onClick={() => void connectTenantKey(manualKeyInput.trim())}
                    className="rounded-lg bg-blue-600 px-2.5 py-1 text-[11px] font-semibold text-white"
                  >
                    接入
                  </button>
                </div>
              </div>
            )}
          </div>
        </aside>

        {/* Main Content */}
        <main className="flex-1 overflow-x-hidden p-5 lg:p-8">
          <div className="mx-auto max-w-6xl">
            {perspective === 'admin' ? (
              !adminToken ? (
                <Card>
                  <p className="text-sm text-slate-500">请在左侧输入平台超管 `X-Admin-Token`，或使用 `--demo-mode` 启动服务端。</p>
                </Card>
              ) : adminTab === 'overview' ? (
                <AdminOverviewPage adminToken={adminToken} onToast={showToast} />
              ) : adminTab === 'tenants' ? (
                <TenantsManagerPage adminToken={adminToken} onToast={showToast} />
              ) : adminTab === 'keys' ? (
                <ApiKeysManagerPage adminToken={adminToken} onToast={showToast} />
              ) : (
                <AuditLogsPage adminToken={adminToken} onToast={showToast} />
              )
            ) : !tenantToken ? (
              <Card>
                <p className="text-sm text-slate-500">请在左侧接入租户 API Key 后使用工作台能力。</p>
              </Card>
            ) : tenantTab === 'kb' ? (
              <KnowledgeBasePage token={tenantToken} onToast={showToast} />
            ) : tenantTab === 'search' ? (
              <RetrievalDebuggerPage token={tenantToken} onToast={showToast} />
            ) : tenantTab === 'chat' ? (
              <GroundedChatPage token={tenantToken} onToast={showToast} />
            ) : tenantTab === 'eval' ? (
              <EvalBenchmarkPage token={tenantToken} onToast={showToast} />
            ) : (
              <IsolationLabPage
                demoTenants={demoData?.tenants || []}
                currentToken={tenantToken}
                onToast={showToast}
              />
            )}
          </div>
        </main>
      </div>

      {toast && (
        <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-xl border border-slate-700 bg-slate-900 px-4 py-2.5 text-xs font-medium text-white shadow-xl">
          {toast}
        </div>
      )}
    </div>
  );
}

export default App;
