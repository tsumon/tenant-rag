import { useEffect, useState } from 'react';
import {
  CheckCircle2,
  Clock,
  Eye,
  FileUp,
  Layers,
  Loader2,
  Lock,
  RefreshCw,
  Trash2,
  Unlock,
} from 'lucide-react';
import { tenantApi } from '../api';
import type { DocumentDetail, DocumentItem, IngestionJob } from '../types';
import { ChunkSourceViewer } from '../components/ChunkSourceViewer';
import { Badge, Card, Modal, formatTime } from '../components/ui';

export function KnowledgeBasePage({
  token,
  onToast,
}: {
  token: string;
  onToast: (msg: string) => void;
}) {
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [jobs, setJobs] = useState<IngestionJob[]>([]);
  const [selectedDetail, setSelectedDetail] = useState<DocumentDetail | null>(null);
  const [highlightRange, setHighlightRange] = useState<{ start: number; end: number } | null>(null);

  // Upload states
  const [mode, setMode] = useState<'file' | 'text'>('file');
  const [fileObj, setFileObj] = useState<File | null>(null);
  const [docId, setDocId] = useState('');
  const [textBody, setTextBody] = useState('');
  const [format, setFormat] = useState<'markdown' | 'text'>('markdown');
  const [maxChars, setMaxChars] = useState(260);
  const [overlap, setOverlap] = useState(40);
  const [dept, setDept] = useState('general');
  const [extraMeta, setExtraMeta] = useState('');
  const [visibility, setVisibility] = useState<'public' | 'restricted'>('public');
  const [allowRoles, setAllowRoles] = useState('hr_director');
  const [asyncJob, setAsyncJob] = useState(true);
  const [uploading, setUploading] = useState(false);

  const loadAll = async () => {
    if (!token) return;
    try {
      const [dRes, jRes] = await Promise.all([
        tenantApi.listDocs(token, 100, 0),
        tenantApi.listJobs(token),
      ]);
      setDocs(dRes.docs);
      setJobs(jRes.jobs);
    } catch (err) {
      onToast(`加载知识库失败：${(err as Error).message}`);
    }
  };

  useEffect(() => {
    void loadAll();
  }, [token]);

  // Poll active background jobs
  useEffect(() => {
    const hasActive = jobs.some((j) => j.status === 'queued' || j.status === 'running');
    if (!hasActive || !token) return;
    const timer = setInterval(() => {
      void loadAll();
    }, 1200);
    return () => clearInterval(timer);
  }, [jobs, token]);

  const inspectDoc = async (id: string) => {
    try {
      const detail = await tenantApi.getDoc(token, id);
      setSelectedDetail(detail);
      if (detail.chunks.length > 0) {
        setHighlightRange({ start: detail.chunks[0].start, end: detail.chunks[0].end });
      } else {
        setHighlightRange(null);
      }
    } catch (err) {
      onToast(`加载文档详情失败：${(err as Error).message}`);
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm(`确认从当前物理分库删除父文档 ${id} 及其全部切片？`)) return;
    try {
      await tenantApi.deleteDoc(token, id);
      onToast(`已删除 ${id}`);
      if (selectedDetail?.doc_id === id) setSelectedDetail(null);
      await loadAll();
    } catch (err) {
      onToast(`删除失败：${(err as Error).message}`);
    }
  };

  const buildMetaAndAcl = () => {
    let custom: Record<string, unknown> = { dept };
    if (extraMeta.trim()) {
      const parsed = JSON.parse(extraMeta);
      if (typeof parsed !== 'object' || Array.isArray(parsed) || !parsed) {
        throw new Error('附加 Metadata 必须是 JSON 对象');
      }
      custom = { ...custom, ...parsed };
    }
    const acl =
      visibility === 'restricted'
        ? {
            visibility: 'restricted',
            allow_roles: allowRoles
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean),
          }
        : { visibility: 'public' };
    return { custom, acl };
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setUploading(true);
    try {
      const { custom, acl } = buildMetaAndAcl();
      if (mode === 'file') {
        if (!fileObj) throw new Error('请先选择待解析文件');
        const fd = new FormData();
        fd.append('file', fileObj);
        if (docId.trim()) fd.append('doc_id', docId.trim());
        fd.append('metadata_json', JSON.stringify(custom));
        fd.append('acl_json', JSON.stringify(acl));
        fd.append('max_chars', String(maxChars));
        fd.append('overlap', String(overlap));
        fd.append('async_job', String(asyncJob));
        const res = await tenantApi.ingestFile(token, fd);
        if (res.mode === 'async' && res.job) {
          onToast(`已提交异步解析入库任务 ${res.job.job_id}`);
        } else {
          onToast(`文件解析并向量化完成`);
        }
        setFileObj(null);
        setDocId('');
      } else {
        if (!textBody.trim()) throw new Error('文本内容不能为空');
        const res = await tenantApi.ingestText(token, {
          text: textBody,
          doc_id: docId.trim() || undefined,
          source: docId.trim() ? `${docId.trim()}.${format === 'markdown' ? 'md' : 'txt'}` : undefined,
          format,
          max_chars: maxChars,
          overlap,
          metadata: custom,
          acl,
        });
        onToast(`入库成功：${res.doc_id}（${res.chunks} 个切片）`);
        setTextBody('');
        setDocId('');
      }
      await loadAll();
    } catch (err) {
      onToast(`入库失败：${(err as Error).message}`);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">知识库入库与切片偏移透视</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            支持 PDF / DOCX / HTML / CSV / JSON / Markdown 多格式解析、文档级 ACL 绑定与异步任务进度追踪
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadAll()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium dark:border-slate-700 dark:bg-slate-800"
        >
          <RefreshCw className="h-3.5 w-3.5" /> 刷新列表
        </button>
      </div>

      {/* Upload & Chunking Card */}
      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <FileUp className="h-4 w-4 text-blue-500" />
            <h3 className="text-sm font-semibold">企业文档入库</h3>
          </div>
          <div className="inline-flex rounded-lg border border-slate-200 p-0.5 text-xs dark:border-slate-700">
            <button
              type="button"
              onClick={() => setMode('file')}
              className={`rounded-md px-3 py-1 font-medium ${
                mode === 'file' ? 'bg-blue-600 text-white' : 'text-slate-500'
              }`}
            >
              多格式文件上传
            </button>
            <button
              type="button"
              onClick={() => setMode('text')}
              className={`rounded-md px-3 py-1 font-medium ${
                mode === 'text' ? 'bg-blue-600 text-white' : 'text-slate-500'
              }`}
            >
              直接文本 / Markdown
            </button>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3 text-xs">
          {mode === 'file' ? (
            <label className="flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-slate-50/60 p-6 transition hover:border-blue-500 dark:border-slate-700 dark:bg-slate-950/50">
              <FileUp className="mb-2 h-6 w-6 text-blue-500" />
              <span className="font-medium text-slate-700 dark:text-slate-200">
                {fileObj ? `已选择：${fileObj.name} (${(fileObj.size / 1024).toFixed(1)} KB)` : '点击选择文件（支持 .pdf, .docx, .md, .txt, .html, .csv, .json）'}
              </span>
              <input
                type="file"
                className="hidden"
                accept=".md,.markdown,.txt,.log,.html,.htm,.csv,.tsv,.json,.jsonl,.docx,.pdf"
                onChange={(e) => setFileObj(e.target.files?.[0] || null)}
              />
            </label>
          ) : (
            <textarea
              rows={5}
              value={textBody}
              onChange={(e) => setTextBody(e.target.value)}
              placeholder="粘贴 Markdown 或纯文本内容…"
              className="w-full rounded-xl border border-slate-300 bg-white p-3 font-mono text-xs dark:border-slate-700 dark:bg-slate-950"
            />
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <label className="mb-1 block text-slate-500">doc_id（可选）</label>
              <input
                value={docId}
                onChange={(e) => setDocId(e.target.value)}
                placeholder="留空自动生成"
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
              />
            </div>
            <div>
              <label className="mb-1 block text-slate-500">部门标签 (metadata.dept)</label>
              <input
                value={dept}
                onChange={(e) => setDept(e.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-950"
              />
            </div>
            <div>
              <label className="mb-1 block text-slate-500">切块长度 / 重叠 ({maxChars} / {overlap})</label>
              <div className="flex gap-2">
                <input
                  type="number"
                  min={80}
                  max={4000}
                  value={maxChars}
                  onChange={(e) => setMaxChars(Number(e.target.value))}
                  className="w-1/2 rounded-lg border border-slate-300 bg-white px-2 py-1.5 dark:border-slate-700 dark:bg-slate-950"
                />
                <input
                  type="number"
                  min={0}
                  max={500}
                  value={overlap}
                  onChange={(e) => setOverlap(Number(e.target.value))}
                  className="w-1/2 rounded-lg border border-slate-300 bg-white px-2 py-1.5 dark:border-slate-700 dark:bg-slate-950"
                />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-slate-500">文档级 ACL 权限</label>
              <div className="flex gap-2">
                <select
                  value={visibility}
                  onChange={(e) => setVisibility(e.target.value as 'public' | 'restricted')}
                  className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 dark:border-slate-700 dark:bg-slate-950"
                >
                  <option value="public">公开 (public)</option>
                  <option value="restricted">受限 (restricted)</option>
                </select>
                {visibility === 'restricted' && (
                  <input
                    value={allowRoles}
                    onChange={(e) => setAllowRoles(e.target.value)}
                    placeholder="允许角色逗号分隔"
                    className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
                  />
                )}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
            <div className="flex items-center gap-3">
              <input
                value={extraMeta}
                onChange={(e) => setExtraMeta(e.target.value)}
                placeholder='附加 JSON 元数据，如 {"year": 2026}'
                className="w-64 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-mono dark:border-slate-700 dark:bg-slate-950"
              />
              {mode === 'file' ? (
                <label className="inline-flex items-center gap-1.5 text-slate-600 dark:text-slate-300">
                  <input
                    type="checkbox"
                    checked={asyncJob}
                    onChange={(e) => setAsyncJob(e.target.checked)}
                    className="rounded-sm"
                  />
                  异步队列任务模式
                </label>
              ) : (
                <select
                  value={format}
                  onChange={(e) => setFormat(e.target.value as 'markdown' | 'text')}
                  className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 dark:border-slate-700 dark:bg-slate-950"
                >
                  <option value="markdown">Markdown 标题切块</option>
                  <option value="text">纯文本段落切块</option>
                </select>
              )}
            </div>
            <button
              type="submit"
              disabled={uploading}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
            >
              {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
              解析并向量化入库
            </button>
          </div>
        </form>
      </Card>

      {/* Async Jobs Bar */}
      {jobs.length > 0 && (
        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              最近异步入库任务队列
            </h3>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {jobs.slice(0, 6).map((j) => (
              <div
                key={j.job_id}
                className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-800 dark:bg-slate-950/60"
              >
                <div className="min-w-0">
                  <div className="truncate font-medium">{j.filename || j.job_id}</div>
                  <div className="text-[11px] text-slate-500 tabular-nums">
                    {j.file_format || j.job_type} · {j.total_chunks} 切片 · {j.progress_pct}%
                  </div>
                </div>
                <Badge
                  variant={
                    j.status === 'succeeded'
                      ? 'emerald'
                      : j.status === 'failed'
                        ? 'rose'
                        : 'blue'
                  }
                >
                  {j.status === 'succeeded' ? (
                    <CheckCircle2 className="h-3 w-3" />
                  ) : j.status === 'running' ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Clock className="h-3 w-3" />
                  )}
                  {j.status}
                </Badge>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Document Inventory */}
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-purple-500" />
            <h3 className="text-sm font-semibold">租户父文档目录（{docs.length} 篇）</h3>
          </div>
          <span className="text-xs text-slate-500">点击任意文档打开左右分栏切片偏移透视器</span>
        </div>

        <div className="space-y-2.5">
          {docs.length === 0 ? (
            <p className="py-6 text-center text-xs text-slate-500">当前租户尚未索引任何文档。</p>
          ) : (
            docs.map((d) => {
              const acl = (d.metadata?._acl as Record<string, unknown> | undefined) ?? undefined;
              const isRestricted = acl?.visibility === 'restricted';
              return (
                <div
                  key={d.doc_id}
                  className="flex flex-col justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50/70 p-3.5 transition hover:border-blue-500/40 dark:border-slate-800 dark:bg-slate-950/60 sm:flex-row sm:items-center"
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">
                        {d.doc_id}
                      </span>
                      <Badge variant="purple">{d.chunk_count} 切片</Badge>
                      <Badge variant={isRestricted ? 'amber' : 'emerald'}>
                        {isRestricted ? <Lock className="h-3 w-3" /> : <Unlock className="h-3 w-3" />}
                        {isRestricted
                          ? `restricted: ${(acl?.allow_roles as string[] | undefined)?.join(', ') || 'explicit'}`
                          : 'public'}
                      </Badge>
                      {Boolean(d.metadata?.dept) && <Badge variant="blue">dept={String(d.metadata.dept)}</Badge>}
                    </div>
                    <p className="line-clamp-2 text-xs text-slate-600 dark:text-slate-400">{d.text}</p>
                    <div className="text-[11px] text-slate-500 tabular-nums">
                      来源: {d.source || 'inline'} · 更新: {formatTime(d.created_at)}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void inspectDoc(d.doc_id)}
                      className="inline-flex items-center gap-1 rounded-lg border border-blue-500/40 bg-blue-500/10 px-3 py-1.5 text-xs font-medium text-blue-600 hover:bg-blue-500/20 dark:text-blue-300"
                    >
                      <Eye className="h-3.5 w-3.5" /> 切片透视
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDelete(d.doc_id)}
                      className="inline-flex items-center gap-1 rounded-lg border border-rose-500/40 px-2.5 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </Card>

      <Modal
        open={Boolean(selectedDetail)}
        title={selectedDetail ? `文档与切片偏移透视 · ${selectedDetail.doc_id}` : ''}
        onClose={() => setSelectedDetail(null)}
        maxWidth="max-w-5xl"
      >
        {selectedDetail && (
          <ChunkSourceViewer
            detail={selectedDetail}
            highlightRange={highlightRange}
            onSelectRange={(start, end) => setHighlightRange({ start, end })}
          />
        )}
      </Modal>
    </div>
  );
}
