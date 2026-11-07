import { useTranslation } from "react-i18next";
import type { AudioFeatures } from "@/api/types";
import { fmtPct, fmtSeconds } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, Kpi } from "@/components/common";

export function AudioInsightsTab({ features, hasAudio, onSeek }: { features: AudioFeatures | null | undefined; hasAudio: boolean; onSeek: (s: number) => void }) {
  const { t } = useTranslation();
  if (!hasAudio) return <EmptyState title={t("card.audio.noAudio")} />;
  if (!features) return <EmptyState title={t("card.audio.unavailable")} />;
  return (
    <div className="flex flex-col gap-4">
      {features.summary_ru ? <div className="rounded-md border border-accent/40 bg-tint/60 px-4 py-3 text-body">{features.summary_ru}</div> : null}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Kpi label={t("card.audio.duration")} value={fmtSeconds(features.duration_s)} />
        <Kpi label={t("card.audio.wpm")} value={features.operator_wpm != null ? Math.round(features.operator_wpm) : "—"} hint={features.speech_rate_label ? `${t("card.audio.rate")}: ${features.speech_rate_label}` : t("card.audio.wpmUnit")} />
        <Kpi label={t("card.audio.pauses")} value={features.long_pauses_count ?? features.long_pauses?.length ?? "—"} />
        <Kpi label={t("card.audio.talkRatio")} value={fmtPct(features.talk_ratio_operator)} hint={features.operator_talk_s != null ? `${fmtSeconds(features.operator_talk_s)} / ${fmtSeconds(features.client_talk_s)}` : undefined} />
        <Kpi label={t("card.audio.overlaps")} value={features.overlaps ?? "—"} />
        <Kpi label={t("card.audio.loudness")} value={features.loudness_range_lu != null ? `${features.loudness_range_lu} LU` : "—"} />
        <Kpi label={t("card.audio.holds")} value={features.hold_segments?.length ?? 0} />
        <Kpi label={t("card.audio.repeated")} value={features.repeated_operator_lines ?? 0} />
      </div>
      {features.long_pauses?.length ? (
        <div className="card">
          <div className="border-b border-line px-4 py-2.5">
            <div className="label">{t("card.audio.pauseList")}</div>
          </div>
          <div className="flex flex-wrap gap-2 p-3">
            {features.long_pauses.map((p, i) => (
              <Button key={i} size="sm" variant="secondary" className="font-mono" onClick={() => onSeek(Math.max(0, p.start - 1))} title={t("card.audio.seekTo", { time: fmtSeconds(p.start) })}>
                {fmtSeconds(p.start)} – {fmtSeconds(p.end)} <Badge variant="warning">{(p.duration ?? p.end - p.start).toFixed(1)} s</Badge>
              </Button>
            ))}
          </div>
        </div>
      ) : null}
      {features.hold_segments?.length ? (
        <div className="card">
          <div className="border-b border-line px-4 py-2.5">
            <div className="label">{t("card.audio.holds")}</div>
          </div>
          <div className="flex flex-wrap gap-2 p-3">
            {features.hold_segments.map((p, i) => (
              <Button key={i} size="sm" variant="secondary" className="font-mono" onClick={() => onSeek(Math.max(0, p.start - 1))}>
                {fmtSeconds(p.start)} – {fmtSeconds(p.end)}
              </Button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
