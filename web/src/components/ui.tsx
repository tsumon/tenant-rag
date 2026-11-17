import React, { useState } from 'react';
import { Check, Copy, X } from 'lucide-react';

export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function formatTime(ts?: number | null): string {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

type BadgeVariant = 'neutral' | 'blue' | 'emerald' | 'amber' | 'rose' | 'purple';

const BADGE_CLASSES: Record<BadgeVariant, string> = {
  neutral: 'bg-slate-200/70 text-slate-700 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700',
  blue: 'bg-blue-500/10 text-blue-700 border-blue-500/30 dark:text-blue-300',
  emerald: 'bg-emerald-500/10 text-emerald-700 border-emerald-500/30 dark:text-emerald-300',
  amber: 'bg-amber-500/10 text-amber-700 border-amber-500/30 dark:text-amber-300',
  rose: 'bg-rose-500/10 text-rose-700 border-rose-500/30 dark:text-rose-300',
  purple: 'bg-purple-500/10 text-purple-700 border-purple-500/30 dark:text-purple-300',
};

export function Badge({
  children,
  variant = 'neutral',
  className = '',
}: {
  children: React.ReactNode;
  variant?: BadgeVariant;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 ${BADGE_CLASSES[variant]} ${className}`}
    >
      {children}
    </span>
  );
}

export function Card({
  children,
  className = '',
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800/90 dark:bg-slate-900/75 ${className}`}
    >
      {children}
    </div>
  );
}

export function StatTile({
  label,
  value,
  sub,
  icon,
  accent = 'blue',
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  icon?: React.ReactNode;
  accent?: 'blue' | 'emerald' | 'amber' | 'rose' | 'purple';
}) {
  const accentBg = {
    blue: 'text-blue-500 bg-blue-500/10',
    emerald: 'text-emerald-500 bg-emerald-500/10',
    amber: 'text-amber-500 bg-amber-500/10',
    rose: 'text-rose-500 bg-rose-500/10',
    purple: 'text-purple-500 bg-purple-500/10',
  }[accent];

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-500 dark:text-slate-400">{label}</div>
          <div className="mt-1.5 text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-50 tabular-nums">
            {value}
          </div>
          {sub && <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{sub}</div>}
        </div>
        {icon && <div className={`rounded-lg p-2 ${accentBg}`}>{icon}</div>}
      </div>
    </Card>
  );
}

export function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // ignore clipboard restrictions
    }
  };
  return (
    <button
      type="button"
      onClick={handleCopy}
      className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? '已复制' : label}
    </button>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
  maxWidth = 'max-w-xl',
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  maxWidth?: string;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-xs">
      <div
        className={`w-full ${maxWidth} rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-800 dark:bg-slate-900`}
      >
        <div className="mb-4 flex items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-800">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
