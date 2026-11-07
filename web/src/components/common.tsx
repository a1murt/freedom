import * as React from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ChevronDown, ChevronRight, Inbox, RefreshCw } from "lucide-react";
import { cn, copyText } from "@/lib/utils";
import { fmtScore, scoreTone, type ScoreTone } from "@/lib/format";
import { errorMessage, notify } from "@/lib/toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/misc";
import { Mascot, type MascotPose } from "@/components/Mascot";
import type { ReviewState } from "@/api/types";

const toneText: Record<ScoreTone, string> = {
  critical: "text-critical",
  warning: "text-warning",
  success: "text-success",
  neutral: "text-text-2",
};
const toneBg: Record<ScoreTone, string> = {
  critical: "bg-critical/12 border-critical/40 text-critical",
  warning: "bg-warning/12 border-warning/40 text-warning",
  success: "bg-success/12 border-success/40 text-success",
  neutral: "bg-surface-2 border-line text-text-2",
};

export function ScoreText({ value, className, digits = 2 }: { value: number | null | undefined; className?: string; digits?: number }) {
  const tone = scoreTone(value);
  return <span className={cn("num font-mono font-medium", toneText[tone], className)}>{fmtScore(value, digits)}</span>;
}

export function ScorePill({ value, className }: { value: number | null | undefined; className?: string }) {
  const tone = scoreTone(value);
  return <span className={cn("num inline-flex min-w-[52px] justify-center rounded-sm border px-1.5 py-0.5 font-mono text-[12px] font-medium", toneBg[tone], className)}>{fmtScore(value)}</span>;
}

export function ScoreRing({ value, original, size = 120, label }: { value: number | null | undefined; original?: number | null; size?: number; label?: string }) {
  const tone = scoreTone(value);
  const r = (size - 10) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value ?? 0)) / 100;
  const stroke = tone === "critical" ? "rgb(var(--critical))" : tone === "warning" ? "rgb(var(--warning))" : tone === "success" ? "rgb(var(--success))" : "rgb(var(--line))";
  const differs = original !== undefined && original !== null && value !== null && value !== undefined && Math.abs(original - value) > 0.004;
  return (
    <div className="relative flex flex-col items-center" style={{ width: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(var(--surface-2))" strokeWidth={6} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={stroke} strokeWidth={6} strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - pct)} style={{ transition: "stroke-dashoffset 600ms ease" }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={cn("num font-display font-semibold leading-none", toneText[tone])} style={{ fontSize: size * 0.27 }}>
          {fmtScore(value)}
        </span>
        {differs ? <span className="num mt-1 font-mono text-[11px] text-text-2 line-through">{fmtScore(original)}</span> : null}
      </div>
      {label ? <span className="label mt-1">{label}</span> : null}
    </div>
  );
}

export function ReviewBadge({ state }: { state: ReviewState | string | null | undefined }) {
  const { t } = useTranslation();
  if (!state || state === "none") return <span className="text-text-2">—</span>;
  const variant = state === "under_review" ? "warning" : state === "changed" ? "info" : state === "confirmed" || state === "reviewed" ? "success" : "neutral";
  return <Badge variant={variant}>{t(`reviewState.${state}` as never, { defaultValue: state })}</Badge>;
}

export function ProviderBadge({ provider, mode, model, className }: { provider: string | null | undefined; mode?: string | null; model?: string | null; className?: string }) {
  const { t } = useTranslation();
  if (!provider) return <span className="text-text-2">—</span>;
  const name = t(`provider.${provider}` as never, { defaultValue: provider });
  const m = model ? model.replace(/^anthropic:/, "") : null;
  const modeLabel = mode ? t(`mode.${mode}` as never, { defaultValue: mode }) : null;
  return (
    <Badge variant={provider === "local" ? "info" : provider === "ensemble" ? "accent" : "neutral"} className={className} title={m ?? undefined}>
      {name}
      {modeLabel && mode !== "single" ? <span className="opacity-70">· {modeLabel}</span> : null}
    </Badge>
  );
}

export function ConfidenceBar({ value, className }: { value: number | null | undefined; className?: string }) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  const tone = v >= 0.8 ? "bg-success" : v >= 0.6 ? "bg-warning" : "bg-critical";
  return (
    <div className={cn("flex items-center gap-2", className)} title={`${Math.round(v * 100)}%`}>
      <div className="h-1 w-16 overflow-hidden rounded-full bg-surface-2">
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${v * 100}%` }} />
      </div>
      <span className="num font-mono text-[11px] text-text-2">{Math.round(v * 100)}%</span>
    </div>
  );
}

export function AnswerLabel({ answer, applicable, answerType }: { answer: string | number | null | undefined; applicable?: boolean; answerType?: string }) {
  const { t } = useTranslation();
  if (applicable === false || answer === null || answer === undefined) return <span className="font-mono text-text-2">{t("common.na")}</span>;
  if (answerType === "scale" || typeof answer === "number" || /^\d+(\.\d+)?$/.test(String(answer))) return <span className="num font-mono">{String(answer)}</span>;
  const a = String(answer).toLowerCase();
  if (a === "yes") return <span className="font-medium text-success">{t("common.yes")}</span>;
  if (a === "no") return <span className="font-medium text-critical">{t("common.no")}</span>;
  if (a === "na" || a === "n/a") return <span className="font-mono text-text-2">{t("common.na")}</span>;
  return <span>{String(answer)}</span>;
}

export function EmptyState({ title, hint, icon, action, className, mascot }: { title: string; hint?: string; icon?: React.ReactNode; action?: React.ReactNode; className?: string; mascot?: MascotPose }) {
  return (
    <div className={cn("glow flex flex-col items-center justify-center gap-2 rounded-md border border-dashed border-line px-6 py-12 text-center", className)}>
      {mascot ? <Mascot pose={mascot} size={112} className="mb-1" /> : <div className="text-text-2">{icon ?? <Inbox className="size-6" />}</div>}
      <div className="text-body font-medium">{title}</div>
      {hint ? <div className="max-w-sm text-caption text-text-2">{hint}</div> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const { t } = useTranslation();
  return (
    <div className={cn("flex items-start gap-3 rounded-md border border-critical/40 bg-critical/8 px-4 py-3 text-caption", className)}>
      <Mascot pose="oops" size={40} decorative className="-my-1 shrink-0" />
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-critical" />
      <div className="flex-1">
        <div className="font-medium text-critical">{t("common.error")}</div>
        <div className="text-text-2">{errorMessage(error)}</div>
      </div>
      {onRetry ? (
        <Button size="sm" variant="ghost" onClick={onRetry}>
          <RefreshCw /> {t("common.retry")}
        </Button>
      ) : null}
    </div>
  );
}

export function LoadingBlock({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-full" style={{ opacity: 1 - i * 0.12 }} />
      ))}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions, className }: { title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-end justify-between gap-3", className)}>
      <div>
        <h1 className="font-display text-h2">{title}</h1>
        {subtitle ? <div className="mt-0.5 text-caption text-text-2">{subtitle}</div> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Section({ title, right, children, className, bodyClassName }: { title?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string; bodyClassName?: string }) {
  return (
    <section className={cn("card", className)}>
      {title !== undefined ? (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <div className="label">{title}</div>
          {right}
        </header>
      ) : null}
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

export function KeyValue({ k, v, mono }: { k: React.ReactNode; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-caption text-text-2">{k}</span>
      <span className={cn("text-caption", mono && "num font-mono")}>{v}</span>
    </div>
  );
}

export function JsonView({ value, maxHeight = 320, className, defaultOpen = false, title }: { value: unknown; maxHeight?: number; className?: string; defaultOpen?: boolean; title?: React.ReactNode }) {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(defaultOpen);
  const text = React.useMemo(() => {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }, [value]);
  return (
    <div className={cn("rounded-sm border border-line bg-bg/60", className)}>
      <div className="flex items-center justify-between px-2 py-1">
        <button type="button" className="flex items-center gap-1 text-caption text-text-2 hover:text-text" onClick={() => setOpen((o) => !o)}>
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          <span>{title ?? "JSON"}</span>
          <span className="num font-mono text-[11px] opacity-60">{text.length.toLocaleString()} b</span>
        </button>
        <Button
          size="sm"
          variant="ghost"
          onClick={async () => {
            if (await copyText(text)) notify(t("common.copied"));
          }}
        >
          {t("common.copy")}
        </Button>
      </div>
      {open ? (
        <pre className="mono overflow-auto whitespace-pre-wrap break-words border-t border-line px-3 py-2 text-[12px] leading-[17px] text-text" style={{ maxHeight }}>
          {text}
        </pre>
      ) : null}
    </div>
  );
}

export function Kpi({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: ScoreTone }) {
  return (
    <div className="card px-4 py-3">
      <div className="label">{label}</div>
      <div className={cn("num mt-1 font-display text-h2", tone ? toneText[tone] : "")}>{value}</div>
      {hint ? <div className="mt-0.5 text-caption text-text-2">{hint}</div> : null}
    </div>
  );
}
