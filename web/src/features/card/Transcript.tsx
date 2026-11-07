import * as React from "react";
import { useTranslation } from "react-i18next";
import { Search, ShieldAlert } from "lucide-react";
import type { Segment, Turn } from "@/api/types";
import { cn } from "@/lib/utils";
import { fmtTurnTime } from "@/lib/format";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

export interface TranscriptHandle {
  scrollToTurn: (idx: number) => void;
}

interface Props {
  turns: Turn[];
  activeTurn: number | null;
  activeSpan: { from: number; to: number } | null;
  evidenceTurns: Set<number>;
  onTurnClick?: (turn: Turn) => void;
  hasAudio?: boolean;
}

const speakerStyles: Record<string, string> = {
  client: "self-end bg-surface-2 border-line text-text",
  operator: "self-start bg-tint/70 border-accent/30 text-text",
  bot: "self-start bg-surface border-line text-text-2 opacity-75",
  system: "self-center bg-transparent border-transparent text-text-2",
};

function highlight(text: string, q: string): React.ReactNode {
  if (!q) return text;
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <mark className="rounded-[2px] bg-warning/40 text-text">{text.slice(i, i + q.length)}</mark>
      {highlight(text.slice(i + q.length), q)}
    </>
  );
}

export const Transcript = React.forwardRef<TranscriptHandle, Props>(function Transcript({ turns, activeTurn, activeSpan, evidenceTurns, onTurnClick, hasAudio }, ref) {
  const { t } = useTranslation();
  const [q, setQ] = React.useState("");
  const [showDup, setShowDup] = React.useState<Set<number>>(new Set());
  const refs = React.useRef(new Map<number, HTMLDivElement>());
  const container = React.useRef<HTMLDivElement>(null);

  React.useImperativeHandle(ref, () => ({
    scrollToTurn: (idx) => {
      const el = refs.current.get(idx);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    },
  }));

  React.useEffect(() => {
    if (activeTurn !== null) {
      const el = refs.current.get(activeTurn);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [activeTurn]);

  const matches = React.useMemo(() => (q ? turns.filter((x) => x.text.toLowerCase().includes(q.toLowerCase())).length : 0), [q, turns]);
  const dupCount = turns.filter((x) => x.is_duplicate).length;

  // Group consecutive turns by segment to draw separators.
  let lastSegment: Segment | null = null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-text-2" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("card.searchTranscript")} className="pl-7" />
        </div>
        {q ? <span className="num text-caption text-text-2">{t("card.turnsMatched", { count: matches })}</span> : null}
        {dupCount > 0 ? <Badge variant="outline">{t("card.duplicates", { count: dupCount })}</Badge> : null}
      </div>
      <div ref={container} className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto px-3 py-3">
        {turns.length === 0 ? <div className="py-8 text-center text-caption text-text-2">{t("card.noTurns")}</div> : null}
        {turns.map((turn) => {
          const segChanged = turn.segment !== lastSegment;
          lastSegment = turn.segment;
          const isActive = activeTurn === turn.idx;
          const inSpan = !!activeSpan && turn.idx >= activeSpan.from && turn.idx <= activeSpan.to;
          const isEvidence = evidenceTurns.has(turn.idx);
          const dupHidden = turn.is_duplicate && !showDup.has(turn.idx);
          const text = turn.pii_masked_text ?? turn.text;
          const matched = q && text.toLowerCase().includes(q.toLowerCase());
          return (
            <React.Fragment key={turn.idx}>
              {segChanged ? (
                <div className="my-2 flex items-center gap-2 text-[11px] uppercase tracking-[0.04em] text-text-2">
                  <div className="h-px flex-1 bg-line" />
                  <span>{t(`card.segments.${turn.segment}` as never, { defaultValue: turn.segment })}</span>
                  <div className="h-px flex-1 bg-line" />
                </div>
              ) : null}
              {dupHidden ? (
                <button
                  type="button"
                  className="self-center rounded-full border border-dashed border-line px-2 py-0.5 font-mono text-[11px] text-text-2 hover:text-text"
                  onClick={() => setShowDup((s) => new Set(s).add(turn.idx))}
                >
                  [{turn.idx}] {t("card.duplicate")} · {t("card.showDuplicate")}
                </button>
              ) : (
                <div
                  ref={(el) => {
                    if (el) refs.current.set(turn.idx, el);
                    else refs.current.delete(turn.idx);
                  }}
                  onClick={() => onTurnClick?.(turn)}
                  className={cn(
                    "group relative max-w-[88%] rounded-md border px-3 py-2 text-caption transition-shadow",
                    speakerStyles[turn.speaker] ?? speakerStyles.client,
                    turn.speaker === "system" && "max-w-full py-1 text-center font-mono text-[11px]",
                    isActive && "turn-highlight",
                    inSpan && !isActive && "turn-span",
                    isEvidence && !isActive && "border-accent/50",
                    turn.is_duplicate && "opacity-60",
                    onTurnClick && hasAudio && turn.offset_seconds !== null && "cursor-pointer",
                    matched && "ring-1 ring-warning/60",
                  )}
                >
                  <div className={cn("mb-0.5 flex items-center gap-2 font-mono text-[11px] text-text-2", turn.speaker === "system" && "justify-center")}>
                    <span className="num">[{turn.idx}]</span>
                    <span className={cn("font-sans font-medium", turn.speaker === "operator" && "text-accent", turn.speaker === "client" && "text-text")}>
                      {t(`card.speakers.${turn.speaker}` as never, { defaultValue: turn.speaker })}
                    </span>
                    <span className="num">{fmtTurnTime(turn.timestamp_raw, turn.offset_seconds)}</span>
                    {turn.pii_masked_text ? (
                      <span className="inline-flex items-center gap-0.5 text-info">
                        <ShieldAlert className="size-3" /> {t("card.pii")}
                      </span>
                    ) : null}
                    {turn.is_duplicate ? <span className="opacity-70">{t("card.duplicate")}</span> : null}
                  </div>
                  <div className={cn("whitespace-pre-wrap break-words leading-[18px]", turn.speaker === "system" && "text-[11px]")}>{highlight(text, q)}</div>
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
});
