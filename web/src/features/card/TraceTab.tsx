import { useTranslation } from "react-i18next";
import { useTrace } from "@/api/queries";
import type { Evaluation, TraceEntry, Vote } from "@/api/types";
import { fmtMoney, fmtMs } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { EmptyState, ErrorState, JsonView, LoadingBlock } from "@/components/common";

export function TraceTab({ evaluation }: { evaluation: Evaluation | null }) {
  const { t } = useTranslation();
  const trace = useTrace(evaluation?.id ?? null);
  if (!evaluation) return <EmptyState title={t("card.noEvaluation")} />;
  if (trace.isLoading) return <LoadingBlock rows={6} />;
  if (trace.error) return <ErrorState error={trace.error} onRetry={() => trace.refetch()} />;
  const items = trace.data ?? [];
  if (items.length === 0) return <EmptyState title={t("card.trace.empty")} />;

  const tokens = items.reduce((s, x) => s + (x.input_tokens ?? 0) + (x.output_tokens ?? 0), 0);
  const cost = items.reduce((s, x) => s + (x.cost_usd ?? 0), 0);
  const latency = items.reduce((s, x) => s + (x.latency_ms ?? 0), 0);

  return (
    <div className="flex flex-col gap-3">
      <div className="text-caption text-text-2">{t("card.trace.totals", { calls: items.length, tokens: tokens.toLocaleString(), cost: fmtMoney(cost, 3), latency: fmtMs(latency) })}</div>
      {items.map((x, i) => (
        <TraceCard key={x.id ?? i} x={x} votes={evaluation.criteria?.find((c) => c.code === x.criterion_code)?.votes ?? x.votes ?? null} />
      ))}
    </div>
  );
}

function TraceCard({ x, votes }: { x: TraceEntry; votes: Vote[] | null }) {
  const { t } = useTranslation();
  const req = x.request as { messages?: unknown; system_chars?: number } | null;
  const messages = req && typeof req === "object" && "messages" in req ? req.messages : x.request;
  return (
    <div className="card">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-2.5 text-caption">
        <span className="font-mono font-semibold">{x.criterion_code}</span>
        <Badge variant={x.kind === "arbiter" ? "accent" : x.kind === "vote" ? "info" : "outline"}>{x.kind ?? "judge"}</Badge>
        <span className="text-text-2">{x.provider}</span>
        <span className="font-mono text-[12px]">{x.model}</span>
        <span className="font-mono text-[11px] text-text-2" title={t("card.trace.promptVersion")}>
          {x.prompt_version}
        </span>
        <span className="ml-auto flex items-center gap-3 font-mono text-[12px] text-text-2">
          <span title={t("card.trace.tokens")} className="num">
            {(x.input_tokens ?? 0).toLocaleString()} → {(x.output_tokens ?? 0).toLocaleString()}
          </span>
          {x.cache_read_tokens ? (
            <span title={t("card.trace.cacheRead")} className={cn("num", "text-accent")}>
              {t("card.trace.cacheRead")} {x.cache_read_tokens.toLocaleString()}
            </span>
          ) : null}
          <span className="num">{fmtMoney(x.cost_usd, 4)}</span>
          <span className="num">{fmtMs(x.latency_ms)}</span>
        </span>
      </div>
      <div className="flex flex-col gap-2 p-3">
        {req?.system_chars ? <div className="text-[11px] text-text-2">system: {req.system_chars.toLocaleString()} chars</div> : null}
        <JsonView value={messages} title={t("card.trace.request")} />
        <JsonView value={x.response} title={t("card.trace.response")} />
        {votes?.length ? (
          <div className="flex flex-wrap items-center gap-1.5 text-caption">
            <span className="label">{t("card.votes")}</span>
            {votes.map((v, i) => (
              <Badge key={i} variant="mono">
                {v.model.replace(/^anthropic:/, "")}: {String(v.answer ?? "—")}
                {v.confidence != null ? ` · ${Math.round(v.confidence * 100)}%` : ""}
              </Badge>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
