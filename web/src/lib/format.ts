export function fmtScore(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

export function fmtMoney(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `$${v.toFixed(digits)}`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function fmtSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined || Number.isNaN(s)) return "—";
  const total = Math.max(0, Math.floor(s));
  const m = Math.floor(total / 60);
  const sec = total % 60;
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}:${String(m % 60).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("ru-RU").format(v);
}

export function fmtDate(iso: string | null | undefined, locale = "ru-RU"): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "short" }).format(d);
}

/** Chat timestamps are MM:SS; call timestamps are ISO. Show both compactly. */
export function fmtTurnTime(raw: string | null | undefined, offset: number | null | undefined): string {
  if (raw && /^\d{1,2}:\d{2}$/.test(raw)) return raw;
  if (offset !== null && offset !== undefined) return fmtSeconds(offset);
  if (raw && raw.includes("T")) {
    const d = new Date(raw);
    if (!Number.isNaN(d.getTime())) return d.toISOString().slice(11, 19);
  }
  return raw ?? "";
}

export type ScoreTone = "critical" | "warning" | "success" | "neutral";

export function scoreTone(v: number | null | undefined): ScoreTone {
  if (v === null || v === undefined || Number.isNaN(v)) return "neutral";
  if (v < 60) return "critical";
  if (v < 85) return "warning";
  return "success";
}

export function truncate(s: string, n = 120): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

/** Plain-text answer label for toasts and timelines (yes/no localized, numbers as-is). */
export function answerText(answer: string | number | null | undefined, t: (k: string) => string): string {
  if (answer === null || answer === undefined || answer === "") return t("common.na");
  const a = String(answer).toLowerCase();
  if (a === "yes") return t("common.yes");
  if (a === "no") return t("common.no");
  if (a === "na" || a === "n/a") return t("common.na");
  return String(answer);
}
