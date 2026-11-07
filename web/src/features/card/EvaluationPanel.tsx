import * as React from "react";
import { useTranslation } from "react-i18next";
import { AlertOctagon } from "lucide-react";
import type { Evaluation, EvaluationCriterion, Evidence } from "@/api/types";
import { fmtDate, fmtMoney, fmtMs, fmtScore } from "@/lib/format";
import { dateLocale } from "@/i18n";
import { cn } from "@/lib/utils";
import { ProviderBadge, ScoreRing } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { CriterionRow } from "./CriterionRow";
import { Mascot } from "@/components/Mascot";

interface Props {
  evaluation: Evaluation;
  onEvidence: (e: Evidence) => void;
  onSpan: (span: { from: number; to: number } | null) => void;
  canAppeal: boolean;
  onAppeal: (c: EvaluationCriterion) => void;
  hasAudio?: boolean;
}

export function EvaluationPanel({ evaluation: ev, onEvidence, onSpan, canAppeal, onAppeal, hasAudio }: Props) {
  const { t } = useTranslation();
  const criteria = ev.criteria ?? [];
  return (
    <div className="flex flex-col gap-4">
      <div className="card flex flex-wrap items-center gap-6 px-5 py-4">
        <ScoreRing value={ev.final_total_score} original={ev.total_score} label={t("card.total")} />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2 text-caption">
            <ProviderBadge provider={ev.provider} mode={ev.mode} model={ev.model} />
            {ev.model ? <span className="font-mono text-[11px] text-text-2">{ev.model.replace(/^anthropic:/, "")}</span> : null}
            {ev.prompt_version ? <span className="font-mono text-[11px] text-text-2">· {ev.prompt_version}</span> : null}
            <span className="text-text-2">· v{ev.checklist_version_id}</span>
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-caption sm:grid-cols-4">
            <Stat k={t("common.cost")} v={fmtMoney(ev.cost_usd, 3)} />
            <Stat k={t("card.trace.tokens")} v={`${(ev.input_tokens ?? 0).toLocaleString()} / ${(ev.output_tokens ?? 0).toLocaleString()}`} />
            <Stat k={t("card.trace.latency")} v={fmtMs(ev.latency_ms)} />
            <Stat k={t("common.date")} v={fmtDate(ev.completed_at ?? ev.created_at, dateLocale())} />
          </div>
          {ev.total_score !== ev.final_total_score ? (
            <div className="text-caption text-text-2">
              {t("card.original")}: <span className="num font-mono line-through">{fmtScore(ev.total_score)}</span> → {t("card.final")}: <span className="num font-mono text-text">{fmtScore(ev.final_total_score)}</span>
            </div>
          ) : null}
        </div>
      </div>

      {ev.status && ev.status !== "completed" ? (
        <div className="flex items-start gap-3 rounded-md border border-warning/50 bg-warning/10 px-4 py-3 text-caption">
          <Badge variant="warning">{t(`status.${ev.status}` as never, { defaultValue: ev.status })}</Badge>
          <span className="text-text-2">{ev.error ?? ""}</span>
        </div>
      ) : null}

      {ev.critical_error ? (
        <div className="flex items-center gap-3 rounded-md border border-critical/50 bg-critical/10 px-4 py-2">
          <Mascot pose="shield" size={56} className="-my-1 shrink-0" />
          <AlertOctagon className="size-5 shrink-0 text-critical" />
          <div>
            <div className="text-body font-medium text-critical">{t("card.grossBanner")}</div>
            <div className="text-caption text-text-2">{t("card.grossBannerHint")}</div>
          </div>
        </div>
      ) : null}

      {ev.sections?.length ? (
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          {ev.sections.map((s) => {
            const pct = s.max > 0 ? (s.score / s.max) * 100 : null;
            return (
              <div key={s.code} className="card px-3 py-2">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] text-text-2">{s.code}</span>
                  <span className="num font-mono text-caption">
                    {fmtScore(s.score, 2)} <span className="text-text-2">/ {fmtScore(s.max, 2)}</span>
                  </span>
                </div>
                <div className="mt-0.5 truncate text-caption" title={s.title}>
                  {s.title}
                </div>
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-surface-2">
                  <div className={cn("h-full rounded-full", pct === null ? "bg-line" : pct >= 85 ? "bg-success" : pct >= 60 ? "bg-warning" : "bg-critical")} style={{ width: `${pct ?? 0}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      <div className="flex flex-col gap-2">
        {criteria.map((c) => (
          <CriterionRow key={c.id} c={c} onEvidence={onEvidence} onSpan={onSpan} canAppeal={canAppeal} onAppeal={onAppeal} hasAudio={hasAudio} />
        ))}
      </div>
    </div>
  );
}

function Stat({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div>
      <div className="label">{k}</div>
      <div className="num font-mono text-caption">{v}</div>
    </div>
  );
}
