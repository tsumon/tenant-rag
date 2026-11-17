import { useEffect, useRef } from 'react';
import { FileText, Layers } from 'lucide-react';
import type { DocumentDetail } from '../types';
import { Badge } from './ui';

export function ChunkSourceViewer({
  detail,
  highlightRange,
  onSelectRange,
}: {
  detail: DocumentDetail;
  highlightRange: { start: number; end: number } | null;
  onSelectRange?: (start: number, end: number) => void;
}) {
  const markRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (markRef.current) {
      markRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [highlightRange?.start, highlightRange?.end, detail.doc_id]);

  const text = detail.text || '';
  const start = highlightRange ? Math.max(0, Math.min(highlightRange.start, text.length)) : 0;
  const end = highlightRange ? Math.max(start, Math.min(highlightRange.end, text.length)) : 0;
  const hasHighlight = Boolean(highlightRange && end > start);

  const acl = (detail.metadata?._acl as Record<string, unknown> | undefined) ?? undefined;
  const visibility = (acl?.visibility as string) || 'public';

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
      {/* Left: Parent full document with exact character offset highlight */}
      <div className="lg:col-span-7 flex flex-col rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950/70">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-2.5 dark:border-slate-800">
          <div className="flex items-center gap-2 text-xs font-semibold text-slate-700 dark:text-slate-200">
            <FileText className="h-4 w-4 text-blue-500" />
            <span>父文档原文 · {detail.source || detail.doc_id}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Badge variant={visibility === 'restricted' ? 'amber' : 'emerald'}>
              ACL: {visibility}
            </Badge>
            {hasHighlight && (
              <Badge variant="blue" className="font-mono">
                offset [{start}, {end})
              </Badge>
            )}
          </div>
        </div>
        <div className="max-h-[420px] overflow-y-auto p-4 text-[13px] leading-relaxed whitespace-pre-wrap break-words text-slate-700 dark:text-slate-300 font-mono">
          {hasHighlight ? (
            <>
              <span>{text.slice(0, start)}</span>
              <mark
                ref={markRef}
                className="rounded-sm bg-amber-300/70 px-0.5 text-slate-950 ring-2 ring-amber-400/80 dark:bg-amber-400/30 dark:text-amber-100 dark:ring-amber-400/60"
              >
                {text.slice(start, end)}
              </mark>
              <span>{text.slice(end)}</span>
            </>
          ) : (
            text
          )}
        </div>
      </div>

      {/* Right: Ordered chunks list */}
      <div className="lg:col-span-5 flex flex-col rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/60">
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5 dark:border-slate-800">
          <div className="flex items-center gap-2 text-xs font-semibold text-slate-700 dark:text-slate-200">
            <Layers className="h-4 w-4 text-purple-500" />
            <span>切片列表（点击定位原文偏移）</span>
          </div>
          <Badge variant="purple">{detail.chunks.length} 块</Badge>
        </div>
        <div className="max-h-[420px] space-y-2 overflow-y-auto p-3">
          {detail.chunks.map((c) => {
            const active =
              Boolean(highlightRange) &&
              c.start >= start &&
              c.end <= end &&
              end > start;
            const exact =
              Boolean(highlightRange) &&
              c.start === start &&
              c.end === end;
            return (
              <button
                key={c.chunk_id}
                type="button"
                onClick={() => onSelectRange?.(c.start, c.end)}
                className={`w-full rounded-lg border p-3 text-left transition ${
                  exact || active
                    ? 'border-blue-500 bg-blue-500/10 ring-1 ring-blue-500/40'
                    : 'border-slate-200 bg-slate-50/70 hover:border-slate-300 dark:border-slate-800 dark:bg-slate-950/60 dark:hover:border-slate-700'
                }`}
              >
                <div className="mb-1 flex items-center justify-between text-[11px] font-mono">
                  <span className="font-semibold text-blue-600 dark:text-blue-400">
                    Chunk #{c.index}
                  </span>
                  <span className="text-slate-500 dark:text-slate-400">
                    [{c.start}, {c.end}) · {c.end - c.start} chars
                  </span>
                </div>
                <div className="line-clamp-3 text-xs text-slate-600 dark:text-slate-300">
                  {c.text}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
