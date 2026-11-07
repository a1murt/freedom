import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";
import { useAnalytics, useChecklists } from "@/api/queries";
import { apiUrl } from "@/lib/config";
import { fmtMoney, fmtMs, fmtPct, fmtScore, fmtSeconds, scoreTone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState, ErrorState, Kpi, LoadingBlock, PageHeader, ScorePill, Section } from "@/components/common";
import { useThemeColors } from "@/lib/useThemeColors";

const ALL = "__all__";

export default function AnalyticsPage() {
  const { t } = useTranslation();
  const checklists = useChecklists();
  const [versionId, setVersionId] = React.useState<string>(ALL);
  const data = useAnalytics(versionId === ALL ? null : Number(versionId));
  const versions = (checklists.data ?? []).flatMap((c) => c.versions.map((v) => ({ ...v, name: c.name })));

  return (
    <div className="flex flex-col gap-4 p-6">
      <PageHeader
        title={t("analytics.title")}
        subtitle={t("analytics.subtitle")}
        actions={
          <>
            <Select value={versionId} onValueChange={setVersionId}>
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t("analytics.allVersions")}</SelectItem>
                {versions.map((v) => (
                  <SelectItem key={v.id} value={String(v.id)}>
                    v{v.version_no} · {v.change_note || v.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="secondary" asChild>
              <a href={apiUrl(`/api/export/evaluations.csv${versionId === ALL ? "" : `?checklist_version_id=${versionId}`}`)} target="_blank" rel="noreferrer">
                {t("analytics.exportCsv")}
              </a>
            </Button>
          </>
        }
      />
      {data.isLoading ? (
        <LoadingBlock rows={8} />
      ) : data.error ? (
        <ErrorState error={data.error} onRetry={() => data.refetch()} />
      ) : !data.data || data.data.count === 0 ? (
        <EmptyState title={t("analytics.empty")} mascot="sleep" />
      ) : (
        <Body d={data.data} />
      )}
    </div>
  );
}

function Body({ d }: { d: NonNullable<ReturnType<typeof useAnalytics>["data"]> }) {
  const { t } = useTranslation();
  const col = useThemeColors();
  const langs = React.useMemo(() => {
    const s = new Set<string>();
    d.criteria_heatmap.forEach((h) => (h.channel === undefined || h.channel === "ALL") && h.language !== "ALL" && s.add(h.language));
    const order = ["RU", "KZ", "EN"];
    return [...s].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  }, [d.criteria_heatmap]);
  const codes = React.useMemo(() => [...new Set(d.criteria_heatmap.map((h) => h.code))].sort(), [d.criteria_heatmap]);
  const cell = (code: string, lang: string) => d.criteria_heatmap.find((h) => h.code === code && h.language === lang && (h.channel === undefined || h.channel === "ALL"));
  const titleOf = (code: string) => d.criteria_heatmap.find((h) => h.code === code)?.title ?? "";
  const fr = d.first_response;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label={t("analytics.kpi.count")} value={d.count} />
        <Kpi label={t("analytics.kpi.mean")} value={fmtScore(d.mean_total)} tone={scoreTone(d.mean_total)} />
        <Kpi label={t("analytics.kpi.median")} value={fmtScore(d.median_total)} tone={scoreTone(d.median_total)} />
        <Kpi label={t("analytics.kpi.critical")} value={d.critical_count} tone={d.critical_count > 0 ? "critical" : "success"} />
        <Kpi label={t("analytics.kpi.firstResponse")} value={fr ? fmtSeconds(fr.mean_seconds) : "—"} hint={fr?.threshold_seconds != null ? `${fmtPct(fr.over_threshold_share)} ${t("analytics.kpi.overThreshold", { sec: fr.threshold_seconds })}` : undefined} />
        <div className="card flex flex-col justify-center gap-1 px-4 py-3">
          <div className="label">{t("analytics.byLanguage")}</div>
          <div className="flex flex-wrap gap-1.5">
            {d.by_language.map((l) => (
              <Badge key={l.language} variant="mono">
                {l.language} {fmtScore(l.mean_total, 1)} <span className="opacity-60">· {l.n}</span>
              </Badge>
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {d.by_channel.map((c) => (
              <Badge key={c.channel} variant="outline">
                {t(`channel.${c.channel}` as never, { defaultValue: c.channel })} {fmtScore(c.mean_total, 1)} <span className="opacity-60">· {c.n}</span>
              </Badge>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_1fr]">
        <Section title={t("analytics.distribution")}>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={d.distribution} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke={col.line} />
                <XAxis dataKey="bucket" tick={{ fill: col["text-2"], fontSize: 12, fontFamily: "SF Mono, ui-monospace, Menlo, monospace" }} axisLine={false} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fill: col["text-2"], fontSize: 12 }} axisLine={false} tickLine={false} />
                <ChartTooltip cursor={{ fill: col["surface-2"] }} contentStyle={{ background: col.surface, border: `1px solid ${col.line}`, borderRadius: 6, fontSize: 12, color: col.text }} labelStyle={{ color: col["text-2"] }} itemStyle={{ color: col.text }} />
                <Bar dataKey="n" name={t("analytics.dialogs")} radius={[4, 4, 0, 0]} isAnimationActive={false}>
                  {d.distribution.map((b) => {
                    const lo = Number(b.bucket.split("-")[0]);
                    const tone = scoreTone(lo);
                    return <Cell key={b.bucket} fill={tone === "critical" ? col.critical : tone === "warning" ? col.warning : col.accent} />;
                  })}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Section>

        <Section title={t("analytics.heatmap")} right={<span className="text-[11px] text-text-2">{t("analytics.heatmapHint")}</span>} bodyClassName="p-0">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{t("analytics.agreementCols.code")}</TableHead>
                {langs.map((l) => (
                  <TableHead key={l} className="text-center">
                    {l}
                  </TableHead>
                ))}
                <TableHead className="text-center">{t("common.all")}</TableHead>
                {d.na_rates ? <TableHead className="text-center">N/A</TableHead> : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {codes.map((code) => {
                const all = cell(code, "ALL");
                const isScale = (all?.value ?? all?.yes_rate_or_mean ?? 0) > 1.0001;
                const na = d.na_rates?.find((n) => n.code === code);
                return (
                  <TableRow key={code}>
                    <TableCell>
                      <span className="font-mono font-semibold">{code}</span> <span className="text-text-2">{titleOf(code)}</span>
                    </TableCell>
                    {langs.map((l) => (
                      <HeatCell key={l} v={cell(code, l)?.value ?? cell(code, l)?.yes_rate_or_mean ?? null} n={cell(code, l)?.n} scale={isScale} />
                    ))}
                    <HeatCell v={all?.value ?? all?.yes_rate_or_mean ?? null} n={all?.n} scale={isScale} strong />
                    {d.na_rates ? <TableCell className="num text-center font-mono text-text-2">{na ? fmtPct(na.na_rate) : "—"}</TableCell> : null}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Section>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Section title={t("analytics.operators")} bodyClassName="p-0">
          <div className="max-h-80 overflow-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("common.operator")}</TableHead>
                  <TableHead className="text-right">n</TableHead>
                  <TableHead className="text-right">{t("analytics.kpi.mean")}</TableHead>
                  <TableHead className="text-right">C8</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...d.operators]
                  .sort((a, b) => (b.mean_total ?? 0) - (a.mean_total ?? 0))
                  .map((o, i) => (
                    <TableRow key={`${o.operator_name}-${i}`}>
                      <TableCell>{o.operator_name ?? <span className="text-text-2">—</span>}</TableCell>
                      <TableCell className="num text-right font-mono text-text-2">{o.n}</TableCell>
                      <TableCell className="text-right">
                        <ScorePill value={o.mean_total} />
                      </TableCell>
                      <TableCell className="text-right">{o.critical_count > 0 ? <Badge variant="critical">{o.critical_count}</Badge> : <span className="text-text-2">0</span>}</TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </div>
        </Section>

        <Section title={t("analytics.worst")} bodyClassName="p-0">
          <div className="max-h-80 overflow-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>ID</TableHead>
                  <TableHead>{t("common.operator")}</TableHead>
                  <TableHead>{t("common.language")}</TableHead>
                  <TableHead className="text-right">{t("common.total")}</TableHead>
                  <TableHead>C8</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {d.worst.map((w) => (
                  <TableRow key={w.conversation_id}>
                    <TableCell>
                      <Link to={`/conversations/${encodeURIComponent(w.conversation_id)}`} className="font-mono font-medium hover:text-accent">
                        {w.conversation_id}
                      </Link>
                    </TableCell>
                    <TableCell>{w.operator_name ?? "—"}</TableCell>
                    <TableCell>
                      <Badge variant="mono">{w.language}</Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <ScorePill value={w.final_total_score ?? w.total_score} />
                    </TableCell>
                    <TableCell>{w.critical_error ? <Badge variant="critical">{t("common.critical")}</Badge> : <span className="text-text-2">—</span>}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Section>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Section title={t("analytics.agreement")} bodyClassName="p-0">
          {d.agreement.every((a) => a.n_reviewed === 0) ? (
            <div className="px-4 py-6 text-center text-caption text-text-2">{t("analytics.noReviews")}</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("analytics.agreementCols.code")}</TableHead>
                  <TableHead className="text-right">{t("analytics.agreementCols.n")}</TableHead>
                  <TableHead className="text-right">{t("analytics.agreementCols.rate")}</TableHead>
                  <TableHead className="text-right">{t("analytics.agreementCols.kappa")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {d.agreement.map((a) => (
                  <TableRow key={a.code}>
                    <TableCell className="font-mono font-semibold">{a.code}</TableCell>
                    <TableCell className="num text-right font-mono">{a.n_reviewed}</TableCell>
                    <TableCell className="num text-right font-mono">{a.agreement_rate != null ? fmtPct(a.agreement_rate) : "—"}</TableCell>
                    <TableCell className="num text-right font-mono">{a.kappa != null ? a.kappa.toFixed(2) : "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>

        <Section title={t("analytics.providers")} bodyClassName="p-0">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{t("analytics.providerCols.provider")}</TableHead>
                <TableHead>{t("analytics.providerCols.model")}</TableHead>
                <TableHead className="text-right">{t("analytics.providerCols.n")}</TableHead>
                <TableHead className="text-right">{t("analytics.providerCols.cost")}</TableHead>
                <TableHead className="text-right">{t("analytics.providerCols.latency")}</TableHead>
                <TableHead className="text-right">{t("analytics.providerCols.total")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {d.providers.map((p, i) => (
                <TableRow key={i}>
                  <TableCell>{t(`provider.${p.provider}` as never, { defaultValue: p.provider })}</TableCell>
                  <TableCell className="font-mono text-[12px]">{p.model.replace(/^anthropic:/, "")}</TableCell>
                  <TableCell className="num text-right font-mono">{p.n}</TableCell>
                  <TableCell className="num text-right font-mono">{fmtMoney(p.mean_cost_usd, 3)}</TableCell>
                  <TableCell className="num text-right font-mono">{fmtMs(p.mean_latency_ms)}</TableCell>
                  <TableCell className="num text-right font-mono">{p.total_cost_usd != null ? fmtMoney(p.total_cost_usd) : "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Section>
      </div>
    </div>
  );
}

function HeatCell({ v, n, scale, strong }: { v: number | null; n?: number; scale: boolean; strong?: boolean }) {
  if (v === null || v === undefined) return <TableCell className="text-center text-text-2">—</TableCell>;
  // Scale criteria: value is a mean in points; normalize by a rough max (20 for C4, 10 for C6) is unknown here,
  // so colour only binary yes-rates and show scale means plainly.
  const frac = scale ? null : v;
  const bg = frac === null ? "transparent" : frac >= 0.85 ? "rgb(var(--success) / 0.22)" : frac >= 0.6 ? "rgb(var(--warning) / 0.22)" : "rgb(var(--critical) / 0.22)";
  return (
    <TableCell className={cn("num text-center font-mono", strong && "font-semibold")} style={{ background: bg }} title={n != null ? `n = ${n}` : undefined}>
      {scale ? v.toFixed(1) : fmtPct(v)}
    </TableCell>
  );
}
