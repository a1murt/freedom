import * as React from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronRight, Radio, XCircle } from "lucide-react";
import { api } from "@/api/endpoints";
import type { Job } from "@/api/types";
import { fmtMoney } from "@/lib/format";
import { notify, notifyError, notifyInfo } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useJobProgress } from "./useJobProgress";
import { Mascot } from "@/components/Mascot";

export function JobProgress({ jobId, onDone, onDismiss, compact }: { jobId: string; onDone?: (status: string, job: Job | null) => void; onDismiss?: () => void; compact?: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [showLog, setShowLog] = React.useState(false);
  const handled = React.useRef(false);
  const state = useJobProgress(jobId, (status, job) => {
    if (handled.current) return;
    handled.current = true;
    void qc.invalidateQueries({ queryKey: ["conversations"] });
    void qc.invalidateQueries({ queryKey: ["evaluation"] });
    void qc.invalidateQueries({ queryKey: ["evaluations"] });
    void qc.invalidateQueries({ queryKey: ["analytics"] });
    if (status === "completed") notify(t("conversations.job.done"), `${job?.done ?? state.done} / ${job?.total ?? state.total} · ${fmtMoney(job?.cost_usd ?? state.cost)}`);
    else if (status === "cancelled") notifyInfo(t("conversations.job.cancelled"));
    else if (status === "failed") notifyError(new Error(job?.log?.at(-1)?.message ?? status), t("conversations.job.failedTitle"));
    onDone?.(status, job);
  });

  const pct = state.total > 0 ? Math.round((state.done / state.total) * 100) : state.status === "completed" ? 100 : 0;
  const terminal = ["completed", "failed", "cancelled", "missing"].includes(state.status);
  const tone = state.status === "failed" ? "critical" : state.failed > 0 ? "warning" : "accent";

  return (
    <div className={cn("card animate-fade-in", compact ? "px-3 py-2" : "px-4 py-3")}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <Mascot pose={state.status === "completed" ? "check" : state.status === "failed" || state.status === "cancelled" ? "oops" : "search"} size={compact ? 28 : 36} decorative className="-my-1 shrink-0" />
          {state.status === "completed" ? <CheckCircle2 className="size-4 text-success" /> : state.status === "failed" || state.status === "cancelled" ? <XCircle className="size-4 text-critical" /> : <Radio className="size-4 animate-pulse-soft text-accent" />}
          <span className="text-caption font-medium">{t("conversations.job.title")}</span>
          <span className="font-mono text-[11px] text-text-2">{jobId}</span>
          <Badge variant={terminal ? (state.status === "completed" ? "success" : "critical") : "accent"}>{t(`common.${state.status}` as never, { defaultValue: state.status })}</Badge>
          {!terminal ? <Badge variant="outline">{state.live ? t("conversations.job.live") : t("conversations.job.polling")}</Badge> : null}
        </div>
        <div className="flex items-center gap-2 text-caption text-text-2">
          <span className="num font-mono">{t("conversations.job.progress", { done: state.done, total: state.total })}</span>
          {state.failed > 0 ? <span className="text-critical">{t("conversations.job.failed", { count: state.failed })}</span> : null}
          <span className="num font-mono">{t("conversations.job.cost", { cost: fmtMoney(state.cost, 2) })}</span>
          {!terminal ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={async () => {
                try {
                  await api.cancelJob(jobId);
                } catch (e) {
                  notifyError(e);
                }
              }}
            >
              {t("common.cancelJob")}
            </Button>
          ) : onDismiss ? (
            <Button size="sm" variant="ghost" onClick={onDismiss}>
              {t("common.close")}
            </Button>
          ) : null}
        </div>
      </div>
      <Progress value={pct} tone={tone} className="mt-2" />
      <div className="mt-1.5 flex items-center justify-between text-[11px] text-text-2">
        <button type="button" className="flex items-center gap-1 hover:text-text" onClick={() => setShowLog((v) => !v)}>
          {showLog ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
          {t("conversations.job.log")} ({state.log.length})
        </button>
        {state.lastConversation ? <span className="font-mono">{state.lastConversation}</span> : null}
      </div>
      {showLog ? (
        <pre className="mono mt-2 max-h-40 overflow-auto rounded-sm border border-line bg-bg/60 px-3 py-2 text-[11px] leading-4 text-text-2">
          {state.log.slice(-60).map((l, i) => (
            <div key={i} className={cn(l.level === "error" && "text-critical")}>
              <span className="opacity-50">{l.ts.slice(11, 19)}</span> {l.message}
            </div>
          ))}
          {state.log.length === 0 ? <span className="opacity-60">…</span> : null}
        </pre>
      ) : null}
    </div>
  );
}
