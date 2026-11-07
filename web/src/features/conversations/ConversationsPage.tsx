import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertOctagon, Download, Mic, Play, Search, Upload, X } from "lucide-react";
import { useConversations } from "@/api/queries";
import { api, type ConversationFilters } from "@/api/endpoints";
import type { ConversationSummary } from "@/api/types";
import { fmtMoney, fmtSeconds } from "@/lib/format";
import { readStorage, writeStorage } from "@/lib/utils";
import { apiUrl } from "@/lib/config";
import { useUi } from "@/store/ui";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState, ErrorState, LoadingBlock, PageHeader, ProviderBadge, ReviewBadge, ScorePill } from "@/components/common";
import { Hint } from "@/components/ui/misc";
import { ImportDialog } from "./ImportDialog";
import { UploadDialog } from "./UploadDialog";
import { EvaluateDialog } from "./EvaluateDialog";
import { JobProgress } from "@/features/jobs/JobProgress";

const ANY = "__any__";

function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

export default function ConversationsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { role } = useUi();

  const [q, setQ] = React.useState("");
  const dq = useDebounced(q);
  const [channel, setChannel] = React.useState(ANY);
  const [language, setLanguage] = React.useState(ANY);
  const [status, setStatus] = React.useState(ANY);
  const [minT, setMinT] = React.useState("");
  const [maxT, setMaxT] = React.useState("");
  const [critical, setCritical] = React.useState(false);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [importOpen, setImportOpen] = React.useState(false);
  const [uploadOpen, setUploadOpen] = React.useState(false);
  const [evalOpen, setEvalOpen] = React.useState<null | { ids: string[] | null }>(null);
  const [jobIds, setJobIds] = React.useState<string[]>(() => {
    try {
      return JSON.parse(readStorage("cqa.jobs") ?? "[]") as string[];
    } catch {
      return [];
    }
  });

  // Pick up jobs still running on the server (e.g. after a page reload).
  React.useEffect(() => {
    api
      .jobs()
      .then((jobs) => {
        const running = jobs.filter((j) => j.status === "running" || j.status === "queued").map((j) => j.id);
        if (running.length) setJobIds((prev) => Array.from(new Set([...prev, ...running])));
      })
      .catch(() => undefined);
  }, []);
  React.useEffect(() => writeStorage("cqa.jobs", JSON.stringify(jobIds)), [jobIds]);

  const filters: ConversationFilters = React.useMemo(
    () => ({
      channel: channel === ANY ? undefined : channel,
      language: language === ANY ? undefined : language,
      status: status === ANY ? undefined : status,
      min_total: minT ? Number(minT) : null,
      max_total: maxT ? Number(maxT) : null,
      critical: critical ? true : null,
      q: dq.trim() || undefined,
    }),
    [channel, language, status, minT, maxT, critical, dq],
  );
  const list = useConversations(filters);
  const items = list.data?.items ?? [];
  const evaluated = items.filter((c) => c.latest_evaluation).length;

  // Client-side safety net in case the backend ignores a filter.
  const visible = React.useMemo(() => {
    return items.filter((c) => {
      const score = c.latest_evaluation?.final_total_score ?? null;
      if (status === "evaluated" && !c.latest_evaluation) return false;
      if (status === "not_evaluated" && c.latest_evaluation) return false;
      if (status === "under_review" && c.review_state !== "under_review") return false;
      if (minT && (score === null || score < Number(minT))) return false;
      if (maxT && (score === null || score > Number(maxT))) return false;
      if (critical && !c.latest_evaluation?.critical_error) return false;
      if (dq.trim()) {
        const s = dq.trim().toLowerCase();
        if (!c.id.toLowerCase().includes(s) && !(c.operator_name ?? "").toLowerCase().includes(s)) return false;
      }
      return true;
    });
  }, [items, status, minT, maxT, critical, dq]);

  const allSelected = visible.length > 0 && visible.every((c) => selected.has(c.id));
  const someSelected = visible.some((c) => selected.has(c.id));
  const toggleAll = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) visible.forEach((c) => next.delete(c.id));
      else visible.forEach((c) => next.add(c.id));
      return next;
    });
  };
  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const resetFilters = () => {
    setQ("");
    setChannel(ANY);
    setLanguage(ANY);
    setStatus(ANY);
    setMinT("");
    setMaxT("");
    setCritical(false);
  };
  const filtersActive = q || channel !== ANY || language !== ANY || status !== ANY || minT || maxT || critical;

  return (
    <div className="flex flex-col gap-4 p-6">
      <PageHeader
        title={t("conversations.title")}
        subtitle={list.data ? t("conversations.subtitle", { total: list.data.total, evaluated }) : undefined}
        actions={
          <>
            <Button variant="secondary" onClick={() => setImportOpen(true)}>
              <Download /> {t("conversations.import")}
            </Button>
            <Button variant="secondary" onClick={() => setUploadOpen(true)}>
              <Upload /> {t("conversations.upload")}
            </Button>
            <Button variant="secondary" asChild>
              <a href={apiUrl("/api/export/evaluations.csv")} target="_blank" rel="noreferrer">
                CSV
              </a>
            </Button>
            {role === "analyst" ? (
              <>
                <Button variant="secondary" onClick={() => setEvalOpen({ ids: null })}>
                  <Play /> {t("conversations.evaluateAll")}
                </Button>
                <Button variant="primary" disabled={selected.size === 0} onClick={() => setEvalOpen({ ids: Array.from(selected) })}>
                  <Play /> {t("conversations.evaluateSelected")} {selected.size ? `(${selected.size})` : ""}
                </Button>
              </>
            ) : null}
          </>
        }
      />

      {jobIds.map((id) => (
        <JobProgress key={id} jobId={id} onDismiss={() => setJobIds((prev) => prev.filter((j) => j !== id))} />
      ))}

      <div className="card flex flex-wrap items-center gap-2 px-3 py-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-text-2" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("conversations.searchPlaceholder")} className="w-56 pl-7" />
        </div>
        <Select value={channel} onValueChange={setChannel}>
          <SelectTrigger className="w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>{t("conversations.filters.channel")}: {t("common.all").toLowerCase()}</SelectItem>
            <SelectItem value="agent chat">{t("channel.agent chat")}</SelectItem>
            <SelectItem value="voice call">{t("channel.voice call")}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={language} onValueChange={setLanguage}>
          <SelectTrigger className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>{t("conversations.filters.language")}: {t("common.all").toLowerCase()}</SelectItem>
            <SelectItem value="RU">RU</SelectItem>
            <SelectItem value="KZ">KZ</SelectItem>
            <SelectItem value="EN">EN</SelectItem>
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>{t("conversations.filters.status")}: {t("common.all").toLowerCase()}</SelectItem>
            <SelectItem value="evaluated">{t("status.evaluated")}</SelectItem>
            <SelectItem value="not_evaluated">{t("status.not_evaluated")}</SelectItem>
            <SelectItem value="under_review">{t("status.under_review")}</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex items-center gap-1 text-caption text-text-2">
          <span>{t("conversations.filters.score")}</span>
          <Input value={minT} onChange={(e) => setMinT(e.target.value.replace(/[^\d.]/g, ""))} placeholder={t("conversations.filters.scoreFrom")} className="w-16 font-mono" inputMode="numeric" />
          <span>–</span>
          <Input value={maxT} onChange={(e) => setMaxT(e.target.value.replace(/[^\d.]/g, ""))} placeholder={t("conversations.filters.scoreTo")} className="w-16 font-mono" inputMode="numeric" />
        </div>
        <label className="flex cursor-pointer items-center gap-1.5 text-caption text-text-2">
          <Checkbox checked={critical} onCheckedChange={(v) => setCritical(v === true)} />
          {t("conversations.filters.critical")}
        </label>
        {filtersActive ? (
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            <X /> {t("conversations.filters.reset")}
          </Button>
        ) : null}
        <div className="ml-auto flex items-center gap-2 text-caption text-text-2">
          {selected.size > 0 ? (
            <>
              <span>{t("common.selected", { count: selected.size })}</span>
              <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                {t("conversations.clearSelection")}
              </Button>
            </>
          ) : (
            <span>{t("common.rows", { count: visible.length })}</span>
          )}
        </div>
      </div>

      {list.isLoading ? (
        <LoadingBlock rows={10} />
      ) : list.error ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : visible.length === 0 ? (
        <EmptyState
          mascot={filtersActive ? undefined : "wave"}
          title={filtersActive ? t("conversations.emptyFiltered") : t("conversations.empty")}
          action={
            filtersActive ? (
              <Button variant="secondary" onClick={resetFilters}>
                {t("conversations.filters.reset")}
              </Button>
            ) : (
              <Button variant="primary" onClick={() => setImportOpen(true)}>
                {t("conversations.import")}
              </Button>
            )
          }
        />
      ) : (
        <div className="card overflow-hidden">
          <div className="max-h-[calc(100vh-280px)] overflow-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-10">
                    <Checkbox checked={allSelected ? true : someSelected ? "indeterminate" : false} onCheckedChange={toggleAll} aria-label={t("conversations.selectAll")} />
                  </TableHead>
                  <TableHead>{t("conversations.columns.id")}</TableHead>
                  <TableHead>{t("conversations.columns.channel")}</TableHead>
                  <TableHead>{t("conversations.columns.language")}</TableHead>
                  <TableHead>{t("conversations.columns.operator")}</TableHead>
                  <TableHead className="text-right">{t("conversations.columns.turns")}</TableHead>
                  <TableHead className="text-right">{t("conversations.columns.firstResponse")}</TableHead>
                  <TableHead className="text-right">{t("conversations.columns.score")}</TableHead>
                  <TableHead>{t("conversations.columns.critical")}</TableHead>
                  <TableHead>{t("conversations.columns.review")}</TableHead>
                  <TableHead>{t("conversations.columns.provider")}</TableHead>
                  <TableHead className="text-right">{t("conversations.columns.cost")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((c) => (
                  <Row key={c.id} c={c} selected={selected.has(c.id)} onToggle={() => toggle(c.id)} onOpen={() => navigate(`/conversations/${encodeURIComponent(c.id)}`)} />
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      <ImportDialog open={importOpen} onOpenChange={setImportOpen} />
      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onCreated={(conv, evaluate) => {
          if (evaluate) setEvalOpen({ ids: [conv.id] });
          else navigate(`/conversations/${encodeURIComponent(conv.id)}`);
        }}
      />
      <EvaluateDialog
        open={!!evalOpen}
        onOpenChange={(o) => !o && setEvalOpen(null)}
        conversationIds={evalOpen?.ids ?? null}
        onStarted={(jobId) => {
          setJobIds((prev) => [jobId, ...prev.filter((j) => j !== jobId)]);
          setSelected(new Set());
        }}
      />
    </div>
  );
}

function Row({ c, selected, onToggle, onOpen }: { c: ConversationSummary; selected: boolean; onToggle: () => void; onOpen: () => void }) {
  const { t } = useTranslation();
  const ev = c.latest_evaluation;
  const running = ev && (ev.status === "running" || ev.status === "queued");
  return (
    <TableRow data-state={selected ? "selected" : undefined} className="cursor-pointer" onClick={onOpen}>
      <TableCell onClick={(e) => e.stopPropagation()}>
        <Checkbox checked={selected} onCheckedChange={onToggle} aria-label={c.id} />
      </TableCell>
      <TableCell className="whitespace-nowrap">
        <Link to={`/conversations/${encodeURIComponent(c.id)}`} className="font-mono text-[12px] font-medium text-text hover:text-accent" onClick={(e) => e.stopPropagation()}>
          {c.id}
        </Link>
      </TableCell>
      <TableCell>
        <span className="inline-flex items-center gap-1.5 text-text-2">
          {c.channel === "voice call" ? <Mic className="size-3.5" /> : null}
          {t(`channel.${c.channel}` as never, { defaultValue: c.channel })}
        </span>
      </TableCell>
      <TableCell>
        <Badge variant="mono">{c.language}</Badge>
      </TableCell>
      <TableCell className="max-w-[160px] truncate">{c.operator_name ?? <span className="text-text-2">—</span>}</TableCell>
      <TableCell className="num text-right font-mono text-text-2">{c.turn_count}</TableCell>
      <TableCell className="num text-right font-mono text-text-2">{fmtSeconds(c.first_response_seconds)}</TableCell>
      <TableCell className="text-right">
        {ev && ev.final_total_score !== null && !running ? (
          <Hint label={ev.total_score !== ev.final_total_score ? `${t("card.original")}: ${ev.total_score?.toFixed(2)}` : undefined}>
            <span>
              <ScorePill value={ev.final_total_score} />
            </span>
          </Hint>
        ) : running ? (
          <Badge variant="accent">{t("status.running")}</Badge>
        ) : ev?.status === "failed" ? (
          <Badge variant="critical">{t("status.failed")}</Badge>
        ) : (
          <span className="text-caption text-text-2">{t("conversations.notEvaluated")}</span>
        )}
      </TableCell>
      <TableCell>
        {ev?.critical_error ? (
          <Badge variant="critical">
            <AlertOctagon className="size-3" /> {t("conversations.gross")}
          </Badge>
        ) : (
          <span className="text-text-2">—</span>
        )}
      </TableCell>
      <TableCell>
        <ReviewBadge state={c.review_state} />
      </TableCell>
      <TableCell>{ev ? <ProviderBadge provider={ev.provider} mode={ev.mode} model={ev.model} /> : <span className="text-text-2">—</span>}</TableCell>
      <TableCell className="num text-right font-mono text-text-2">{ev?.cost_usd != null ? fmtMoney(ev.cost_usd, 3) : "—"}</TableCell>
    </TableRow>
  );
}
