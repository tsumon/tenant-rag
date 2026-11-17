import { useState } from 'react';
import {
  AlertTriangle,
  Bot,
  BookOpen,
  Send,
  Settings2,
  ShieldCheck,
  User,
  X,
} from 'lucide-react';
import { streamChat, tenantApi } from '../api';
import type { CitationItem, DocumentDetail } from '../types';
import { ChunkSourceViewer } from '../components/ChunkSourceViewer';
import { Badge, Card } from '../components/ui';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  abstained?: boolean;
  mode?: string;
  citations?: CitationItem[];
  latency_ms?: number;
}

const PRESET_QUESTIONS = [
  {
    label: 'SLA 赔付标准',
    query: '星辰云科企业版月度可用性 SLA 是多少？低于标准怎么赔付？',
  },
  {
    label: '一线城市差旅住宿上限',
    query: '一线城市出差每晚住宿报销上限是多少元？餐饮补贴多少？',
  },
  {
    label: '受限绝密薪酬（测 ACL）',
    query: '核心高管年终绩效基数和留任特别奖金方案是什么？',
  },
  {
    label: '越界无答案问题（测拒答）',
    query: 'NASA 阿尔忒弥斯登月火箭的发射推进剂型号是什么？',
  },
];

export function GroundedChatPage({
  token,
  onToast,
}: {
  token: string;
  onToast: (msg: string) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [roles, setRoles] = useState('');
  const [showLlm, setShowLlm] = useState(false);
  const [llmBaseUrl, setLlmBaseUrl] = useState('');
  const [llmApiKey, setLlmApiKey] = useState('');
  const [llmModel, setLlmModel] = useState('gpt-4o-mini');

  const [drawerCitation, setDrawerCitation] = useState<{
    citation: CitationItem;
    detail: DocumentDetail;
  } | null>(null);

  const openCitationDrawer = async (c: CitationItem) => {
    try {
      const detail = await tenantApi.getDoc(token, c.parent_id);
      setDrawerCitation({ citation: c, detail });
    } catch (err) {
      onToast(`加载引用原文失败：${(err as Error).message}`);
    }
  };

  const sendMessage = async (textOverride?: string) => {
    const q = (textOverride ?? input).trim();
    if (!q || streaming) return;
    if (!textOverride) setInput('');

    const userMsg: ChatMessage = { id: `u_${Date.now()}`, role: 'user', content: q };
    const assistantId = `a_${Date.now()}`;
    const placeholder: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      citations: [],
    };
    setMessages((prev) => [...prev, userMsg, placeholder]);
    setStreaming(true);

    try {
      const payload: Record<string, unknown> = {
        query: q,
        k: 4,
        reranker: 'lexical',
        context_window: 1,
        check_abstention: true,
        min_vector_score: 0.28,
        principal: {
          user_id: 'workspace-user',
          roles: roles
            .split(',')
            .map((r) => r.trim())
            .filter(Boolean),
          is_admin: false,
        },
      };
      if (llmBaseUrl.trim() && llmApiKey.trim()) {
        payload.llm = {
          base_url: llmBaseUrl.trim(),
          api_key: llmApiKey.trim(),
          model: llmModel.trim() || 'gpt-4o-mini',
        };
      }

      await streamChat(token, payload, {
        onRetrieval: (data) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    latency_ms: Number(data.latency_ms ?? 0),
                    citations: (data.citations as CitationItem[]) || [],
                  }
                : m,
            ),
          );
        },
        onDelta: (delta) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, content: m.content + delta } : m,
            ),
          );
        },
        onCitations: (data) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    citations: (data.citations as CitationItem[]) || [],
                    abstained: Boolean(data.abstained),
                    mode: String(data.mode || 'extractive_offline'),
                  }
                : m,
            ),
          );
        },
      });
    } catch (err) {
      onToast(`问答失败：${(err as Error).message}`);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, content: `请求出错：${(err as Error).message}` }
            : m,
        ),
      );
    } finally {
      setStreaming(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">带引用溯源的 Grounded 问答台</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            先经过租户物理检索、ACL 鉴权与证据充足性门控，再流式输出带 `[1]` 原文偏移角标的严谨答复
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <div className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-900">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />
            <span className="text-slate-500">当前角色:</span>
            <select
              value={roles}
              onChange={(e) => setRoles(e.target.value)}
              className="bg-transparent font-mono font-medium focus:outline-none"
            >
              <option value="">普通员工 (无受限权限)</option>
              <option value="hr_director">HR 总监 (hr_director)</option>
              <option value="compliance_officer">合规官 (compliance_officer)</option>
            </select>
          </div>
          <button
            type="button"
            onClick={() => setShowLlm((v) => !v)}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-medium dark:border-slate-700 dark:bg-slate-800"
          >
            <Settings2 className="h-3.5 w-3.5" /> LLM 端点配置
          </button>
        </div>
      </div>

      {showLlm && (
        <Card className="p-4 text-xs">
          <div className="mb-2 font-semibold">可选 OpenAI 兼容生成模型（留空则使用内置零密钥抽取式引用合成模式）</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <input
              value={llmBaseUrl}
              onChange={(e) => setLlmBaseUrl(e.target.value)}
              placeholder="Base URL，如 https://api.openai.com/v1"
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
            />
            <input
              type="password"
              value={llmApiKey}
              onChange={(e) => setLlmApiKey(e.target.value)}
              placeholder="API Key"
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
            />
            <input
              value={llmModel}
              onChange={(e) => setLlmModel(e.target.value)}
              placeholder="Model 名，如 gpt-4o-mini"
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
            />
          </div>
        </Card>
      )}

      {/* Preset Enterprise Test Queries */}
      <div className="flex flex-wrap gap-2">
        {PRESET_QUESTIONS.map((p) => (
          <button
            key={p.label}
            type="button"
            disabled={streaming}
            onClick={() => void sendMessage(p.query)}
            className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-700 transition hover:border-blue-500 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:text-blue-300"
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className={`grid grid-cols-1 gap-4 ${drawerCitation ? 'lg:grid-cols-12' : ''}`}>
        {/* Main Chat Area */}
        <Card className={`${drawerCitation ? 'lg:col-span-6' : ''} flex h-[540px] flex-col p-0 overflow-hidden`}>
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center text-center text-xs text-slate-500">
                <Bot className="mb-2 h-8 w-8 text-blue-500/70" />
                <p className="font-medium text-slate-700 dark:text-slate-300">点击上方预设场景问题，或输入业务提问开始 SSE 流式问答</p>
                <p className="mt-1">可切换右上角「当前角色」对比普通员工与 HR 总监对受限文档的回答差异</p>
              </div>
            ) : (
              messages.map((m) => (
                <div key={m.id} className={`flex gap-3 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  {m.role === 'assistant' && (
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-blue-600/15 text-blue-500">
                      <Bot className="h-4 w-4" />
                    </div>
                  )}
                  <div
                    className={`max-w-[85%] rounded-2xl px-4 py-3 text-xs leading-relaxed ${
                      m.role === 'user'
                        ? 'bg-blue-600 text-white'
                        : 'border border-slate-200 bg-slate-50 text-slate-800 dark:border-slate-800 dark:bg-slate-950/70 dark:text-slate-200'
                    }`}
                  >
                    <div className="whitespace-pre-wrap">
                      {!m.content && streaming
                        ? '正在检索与校验证据…'
                        : m.role === 'assistant' && !m.abstained && m.citations?.length
                          ? m.content.split(/(\[\d+\])/g).map((part, idx) => {
                              const match = /^\[(\d+)\]$/.exec(part);
                              if (!match) return <span key={idx}>{part}</span>;
                              const num = Number(match[1]);
                              const cit = m.citations?.find((c) => c.index === num);
                              if (!cit) return <span key={idx}>{part}</span>;
                              return (
                                <button
                                  key={idx}
                                  type="button"
                                  onClick={() => void openCitationDrawer(cit)}
                                  className="mx-0.5 inline-flex items-center rounded-sm bg-blue-500/20 px-1 py-0.2 font-mono text-[11px] font-semibold text-blue-600 hover:bg-blue-500/30 dark:text-blue-300"
                                  title={`查看引用 [${cit.index}] 原文偏移 [${cit.start}, ${cit.end})`}
                                >
                                  {part}
                                </button>
                              );
                            })
                          : m.content}
                    </div>

                    {m.role === 'assistant' && (m.abstained || (m.citations && m.citations.length > 0)) && (
                      <div className="mt-3 border-t border-slate-200 pt-2.5 dark:border-slate-800">
                        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                          {m.abstained ? (
                            <Badge variant="amber">
                              <AlertTriangle className="h-3 w-3" /> 已安全拒答 (Abstention Gate)
                            </Badge>
                          ) : (
                            <Badge variant="emerald">证据已核验 · {m.mode || 'grounded'}</Badge>
                          )}
                          {m.latency_ms !== undefined && (
                            <span className="font-mono text-[11px] text-slate-500">检索 {m.latency_ms} ms</span>
                          )}
                        </div>
                        {m.citations && m.citations.length > 0 && !m.abstained && (
                          <div className="flex flex-wrap gap-1.5">
                            {m.citations.map((c) => (
                              <button
                                key={`${c.doc_id}_${c.index}`}
                                type="button"
                                onClick={() => void openCitationDrawer(c)}
                                className="inline-flex items-center gap-1 rounded-md border border-blue-500/40 bg-blue-500/10 px-2 py-1 font-mono text-[11px] font-medium text-blue-600 hover:bg-blue-500/20 dark:text-blue-300"
                              >
                                <BookOpen className="h-3 w-3" /> [{c.index}] {c.source} [{c.start},{c.end})
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  {m.role === 'user' && (
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                      <User className="h-4 w-4" />
                    </div>
                  )}
                </div>
              ))
            )}
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void sendMessage();
            }}
            className="flex items-center gap-2 border-t border-slate-200 p-3 dark:border-slate-800"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="输入业务问题，支持基于当前租户知识的 SSE 流式引用问答…"
              className="flex-1 rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-xs dark:border-slate-700 dark:bg-slate-950"
            />
            <button
              type="submit"
              disabled={streaming}
              className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
            >
              <Send className="h-3.5 w-3.5" /> 发送
            </button>
          </form>
        </Card>

        {/* Source Evidence Drawer */}
        {drawerCitation && (
          <Card className="lg:col-span-6 flex h-[540px] flex-col p-0 overflow-hidden">
            <div className="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
              <div className="text-xs font-semibold">
                引用溯源证据抽屉 · 角标 [{drawerCitation.citation.index}]
              </div>
              <button
                type="button"
                onClick={() => setDrawerCitation(null)}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <ChunkSourceViewer
                detail={drawerCitation.detail}
                highlightRange={{
                  start: drawerCitation.citation.start,
                  end: drawerCitation.citation.end,
                }}
              />
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
