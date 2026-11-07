import * as React from "react";
import { useTranslation } from "react-i18next";
import { Pause, Play } from "lucide-react";
import type { AudioFeatures } from "@/api/types";
import { apiUrl } from "@/lib/config";
import { fmtPct, fmtSeconds } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export interface AudioHandle {
  seek: (seconds: number, play?: boolean) => void;
}

const SPEEDS = ["0.75", "1", "1.25", "1.5", "2"];

export const AudioPlayer = React.forwardRef<AudioHandle, { conversationId: string; features?: AudioFeatures | null; markers?: number[] }>(function AudioPlayer({ conversationId, features, markers = [] }, ref) {
  const { t } = useTranslation();
  const audio = React.useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = React.useState(false);
  const [time, setTime] = React.useState(0);
  const [duration, setDuration] = React.useState<number>(features?.duration_s ?? 0);
  const [speed, setSpeed] = React.useState("1");
  const [error, setError] = React.useState(false);

  React.useImperativeHandle(ref, () => ({
    seek: (seconds, play = true) => {
      const a = audio.current;
      if (!a) return;
      a.currentTime = Math.max(0, seconds);
      if (play) void a.play().catch(() => undefined);
    },
  }));

  React.useEffect(() => {
    if (audio.current) audio.current.playbackRate = Number(speed);
  }, [speed]);

  React.useEffect(() => {
    if (features?.duration_s && !duration) setDuration(features.duration_s);
  }, [features?.duration_s, duration]);

  const toggle = () => {
    const a = audio.current;
    if (!a) return;
    if (a.paused) void a.play().catch(() => setError(true));
    else a.pause();
  };

  const onBarClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = (e.clientX - rect.left) / rect.width;
    if (audio.current && duration) {
      audio.current.currentTime = frac * duration;
      void audio.current.play().catch(() => undefined);
    }
  };

  const pct = duration ? (time / duration) * 100 : 0;

  return (
    <div className="border-b border-line px-3 py-2">
      <audio
        ref={audio}
        src={apiUrl(`/api/audio/${encodeURIComponent(conversationId)}`)}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || duration)}
        onError={() => setError(true)}
      />
      <div className="flex items-center gap-2">
        <Button variant={playing ? "secondary" : "primary"} size="icon" onClick={toggle} aria-label={t("card.audio.player")} disabled={error}>
          {playing ? <Pause /> : <Play />}
        </Button>
        <div className="num w-[92px] shrink-0 font-mono text-[12px] text-text-2">
          {fmtSeconds(time)} / {fmtSeconds(duration)}
        </div>
        <div className="group relative h-6 flex-1 cursor-pointer" onClick={onBarClick} title={t("card.audio.player")}>
          <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-surface-2" />
          {(features?.long_pauses ?? []).map((p, i) =>
            duration ? (
              <div key={`p${i}`} className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-warning/50" style={{ left: `${(p.start / duration) * 100}%`, width: `${Math.max(0.5, ((p.end - p.start) / duration) * 100)}%` }} title={`${fmtSeconds(p.start)}–${fmtSeconds(p.end)}`} />
            ) : null,
          )}
          {(features?.hold_segments ?? []).map((p, i) =>
            duration ? (
              <div key={`h${i}`} className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-info/50" style={{ left: `${(p.start / duration) * 100}%`, width: `${Math.max(0.5, ((p.end - p.start) / duration) * 100)}%` }} />
            ) : null,
          )}
          <div className="absolute left-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-accent" style={{ width: `${pct}%` }} />
          {markers.map((m, i) =>
            duration ? <div key={`m${i}`} className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 bg-accent/80" style={{ left: `${(m / duration) * 100}%` }} /> : null,
          )}
          <div className={cn("absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bg bg-accent shadow", !duration && "hidden")} style={{ left: `${pct}%` }} />
        </div>
        <Select value={speed} onValueChange={setSpeed}>
          <SelectTrigger className="w-[72px]" aria-label={t("card.audio.speed")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SPEEDS.map((s) => (
              <SelectItem key={s} value={s}>
                {s}×
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {error ? <div className="mt-1 text-[11px] text-critical">{t("card.audio.noAudio")}</div> : null}
      {features ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {features.summary_ru ? <Badge variant="accent">{features.summary_ru}</Badge> : null}
          {features.operator_wpm != null ? (
            <Badge variant="mono">
              {t("card.audio.wpm")}: {Math.round(features.operator_wpm)} {t("card.audio.wpmUnit")}
            </Badge>
          ) : null}
          {features.long_pauses_count != null || features.long_pauses ? (
            <Badge variant="mono">
              {t("card.audio.pauses")}: {features.long_pauses_count ?? features.long_pauses.length}
            </Badge>
          ) : null}
          {features.talk_ratio_operator != null ? (
            <Badge variant="mono">
              {t("card.audio.talkRatio")}: {fmtPct(features.talk_ratio_operator)}
            </Badge>
          ) : null}
        </div>
      ) : null}
    </div>
  );
});
