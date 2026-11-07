import * as React from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, FileDown, FileUp, GitCompare, Play, Plus, RefreshCw, Save } from "lucide-react";
import { api } from "@/api/endpoints";
import { useChecklists, useVersion } from "@/api/queries";
import type { ChecklistVersion } from "@/api/types";
import { downloadText } from "@/lib/utils";
import { notify, notifyError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ErrorState, JsonView, KeyValue, LoadingBlock, Section } from "@/components/common";
import { CriterionEditor } from "./CriterionEditor";
import { DiffDialog } from "./DiffDialog";
import { blankCriterion, criteriaEqual, exportVersionJson, stripCriterion, toEditable, type EditableCriterion } from "./model";
import { EvaluateDialog } from "@/features/conversations/EvaluateDialog";
import { JobProgress } from "@/features/jobs/JobProgress";
import { Mascot } from "@/components/Mascot";

export default function ChecklistEditorPage() {
  const { t } = useTranslation();
  const { id, vid } = useParams<{ id: string; vid: string }>();
  const checklistId = Number(id);
  const versionId = Number(vid);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const lists = useChecklists();
  const checklist = lists.data?.find((c) => c.id === checklistId) ?? null;
  const version = useVersion(checklistId, versionId);

  const [criteria, setCriteria] = React.useState<EditableCriterion[]>([]);
  const [baseline, setBaseline] = React.useState<EditableCriterion[]>([]);
  const [advancedKey, setAdvancedKey] = React.useState<string | null>(null);
  const [saveOpen, setSaveOpen] = React.useState(false);
  const [changeNote, setChangeNote] = React.useState("");
  const [compile, setCompile] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [diffOpen, setDiffOpen] = React.useState(false);
  const [applyOpen, setApplyOpen] = React.useState(false);
  const [jobId, setJobId] = React.useState<string | null>(null);
  const [regenerating, setRegenerating] = React.useState(false);
  const fileInput = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (version.data) {
      const e = toEditable(version.data);
      setCriteria(e);
      setBaseline(e);
    }
  }, [version.data]);

  const dirty = React.useMemo(() => !criteriaEqual(criteria, baseline), [criteria, baseline]);
  const v = version.data;
  const sections = v?.sections ?? [];
  const advanced = criteria.find((c) => c._key === advancedKey) ?? null;

  const update = (key: string, next: EditableCriterion) => setCriteria((list) => list.map((c) => (c._key === key ? next : c)));
  const move = (key: string, dir: -1 | 1) =>
    setCriteria((list) => {
      const i = list.findIndex((c) => c._key === key);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= list.length) return list;
      const next = list.slice();
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  const remove = (key: string) => setCriteria((list) => list.filter((c) => c._key !== key));
  const add = () => {
    const n = criteria.length + 1;
    setCriteria((list) => [...list, blankCriterion(`C${n}`, sections[0]?.code ?? "S1")]);
  };

  const save = async () => {
    if (!v) return;
    setSaving(true);
    try {
      const created = await api.createVersion(checklistId, {
        scoring_rules: v.scoring_rules,
        sections: v.sections,
        criteria: criteria.map((c, i) => stripCriterion(c, i)),
        change_note: changeNote.trim() || `v${v.version_no + 1}`,
        compile_specs: compile,
      });
      notify(t("checklists.editor.saved", { no: created.version_no }));
      setSaveOpen(false);
      setChangeNote("");
      void qc.invalidateQueries({ queryKey: ["checklists"] });
      navigate(`/checklists/${checklistId}/versions/${created.id}`);
    } catch (e) {
      notifyError(e);
    } finally {
      setSaving(false);
    }
  };

  const activate = async () => {
    if (!v) return;
    try {
      await api.activate(checklistId, v.id);
      notify(t("checklists.editor.activated", { no: v.version_no }));
      void qc.invalidateQueries({ queryKey: ["checklists"] });
      void qc.invalidateQueries({ queryKey: ["version", checklistId, v.id] });
    } catch (e) {
      notifyError(e);
    }
  };

  const regenerate = async () => {
    if (!v || !advanced) return;
    setRegenerating(true);
    try {
      const crit = await api.regenerateSpec(checklistId, v.id, advanced.code);
      update(advanced._key, { ...advanced, judge_spec: crit.judge_spec });
      setBaseline((b) => b.map((c) => (c._key === advanced._key ? { ...c, judge_spec: crit.judge_spec } : c)));
      notify(t("checklists.editor.regenerated"));
    } catch (e) {
      notifyError(e);
    } finally {
      setRegenerating(false);
    }
  };

  const importFile = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text()) as Partial<ChecklistVersion>;
      if (!Array.isArray(parsed.criteria)) throw new Error(t("checklists.editor.importBad"));
      setCriteria(toEditable({ ...(v as ChecklistVersion), criteria: parsed.criteria } as ChecklistVersion));
      notify(t("checklists.editor.importOk"));
    } catch (e) {
      notifyError(e);
    }
  };

  if (version.isLoading || lists.isLoading) return <LoadingBlock rows={8} className="p-6" />;
  if (version.error) return <ErrorState error={version.error} onRetry={() => version.refetch()} className="m-6" />;
  if (!v) return null;

  return (
    <div className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/checklists")} aria-label={t("common.back")}>
          <ArrowLeft />
        </Button>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-display text-h3">{checklist?.name ?? t("checklists.editor.title")}</h1>
            <Select value={String(v.id)} onValueChange={(val) => navigate(`/checklists/${checklistId}/versions/${val}`)}>
              <SelectTrigger className="w-auto min-w-[140px] font-mono">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[...(checklist?.versions ?? [])]
                  .sort((a, b) => b.version_no - a.version_no)
                  .map((x) => (
                    <SelectItem key={x.id} value={String(x.id)}>
                      v{x.version_no} · {x.change_note || "—"}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            {v.is_active ? (
              <Badge variant="accent">
                <CheckCircle2 className="size-3" /> {t("common.active")}
              </Badge>
            ) : (
              <Button size="sm" variant="secondary" onClick={activate}>
                {t("common.activate")}
              </Button>
            )}
            {dirty ? <Badge variant="warning">{t("checklists.editor.dirty")}</Badge> : null}
          </div>
          <div className="text-caption text-text-2">{v.change_note}</div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <input ref={fileInput} type="file" accept="application/json,.json" className="hidden" onChange={(e) => e.target.files?.[0] && void importFile(e.target.files[0])} />
          <Button variant="ghost" size="sm" onClick={() => fileInput.current?.click()}>
            <FileUp /> {t("checklists.editor.importJson")}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => downloadText(`checklist-v${v.version_no}.json`, exportVersionJson(v, criteria, checklist?.name))}>
            <FileDown /> {t("checklists.editor.exportJson")}
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setDiffOpen(true)} disabled={(checklist?.versions.length ?? 0) < 2}>
            <GitCompare /> {t("checklists.editor.diff")}
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setApplyOpen(true)} disabled={dirty} title={dirty ? t("checklists.editor.unsavedWarning") : undefined}>
            <Play /> {t("checklists.editor.apply")}
          </Button>
          <Button variant="primary" size="sm" onClick={() => setSaveOpen(true)}>
            <Save /> {t("checklists.editor.saveAsVersion")}
          </Button>
        </div>
      </div>

      {jobId ? <JobProgress jobId={jobId} onDismiss={() => setJobId(null)} /> : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_300px]">
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="label">
              {t("checklists.editor.criteria")} · {criteria.length}
            </span>
            <Button size="sm" variant="secondary" onClick={add}>
              <Plus /> {t("checklists.editor.addCriterion")}
            </Button>
          </div>
          {criteria.map((c, i) => (
            <CriterionEditor key={c._key} c={c} index={i} count={criteria.length} sections={sections} onChange={(n) => update(c._key, n)} onMove={(d) => move(c._key, d)} onRemove={() => remove(c._key)} onAdvanced={() => setAdvancedKey(c._key)} />
          ))}
        </div>
        <div className="flex flex-col gap-4">
          <Section title={t("checklists.editor.scoringRules")} bodyClassName="px-4 py-2">
            <KeyValue k="total_scale" v={String(v.scoring_rules.total_scale ?? "—")} mono />
            <KeyValue k="na_policy" v={String(v.scoring_rules.na_policy ?? "—")} mono />
            <KeyValue k="rounding" v={v.scoring_rules.rounding ? `${v.scoring_rules.rounding.mode} · ${v.scoring_rules.rounding.decimals}` : "—"} mono />
            <KeyValue k="critical" v={String(v.scoring_rules.critical?.mode ?? "—")} mono />
            <KeyValue k="no_applicable_policy" v={String(v.scoring_rules.no_applicable_policy ?? "—")} mono />
            <KeyValue k="first_response_threshold" v={v.scoring_rules.first_response_threshold_seconds != null ? `${v.scoring_rules.first_response_threshold_seconds} s` : "—"} mono />
            {v.scoring_rules.excluded_from_score?.length ? (
              <div className="mt-1 flex flex-wrap gap-1">
                {v.scoring_rules.excluded_from_score.map((x) => (
                  <Badge key={x} variant="outline" className="font-mono text-[10px]">
                    {x}
                  </Badge>
                ))}
              </div>
            ) : null}
            <div className="mt-2">
              <JsonView value={v.scoring_rules} title="scoring_rules" />
            </div>
          </Section>
          <Section title={t("checklists.editor.sections")} bodyClassName="px-4 py-2">
            {sections.map((s) => (
              <KeyValue key={s.code} k={<span className="font-mono">{s.code}</span>} v={s.title} />
            ))}
          </Section>
          <Section title={t("common.weight")} bodyClassName="px-4 py-2">
            {criteria.map((c) => (
              <div key={c._key} className="flex items-center gap-2 py-0.5 text-caption">
                <span className="w-8 font-mono text-text-2">{c.code}</span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
                  <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, (Number(c.weight) / Math.max(1, criteria.reduce((s, x) => s + Number(x.weight || 0), 0))) * 100)}%` }} />
                </div>
                <span className="num w-8 text-right font-mono text-text-2">{c.weight}</span>
              </div>
            ))}
            <div className="mt-1 text-right font-mono text-[11px] text-text-2">
              Σ {criteria.reduce((s, x) => s + Number(x.weight || 0), 0)}
            </div>
          </Section>
        </div>
      </div>

      {/* Advanced drawer */}
      <Dialog open={!!advanced} onOpenChange={(o) => !o && setAdvancedKey(null)}>
        <DialogContent side="right" width="720px">
          <DialogHeader>
            <DialogTitle>
              {t("checklists.editor.advanced")} · <span className="font-mono">{advanced?.code}</span>
            </DialogTitle>
            <DialogDescription>{t("checklists.editor.judgeSpec")}</DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            {advanced?.judge_spec ? (
              <>
                {advanced.judge_spec.definition ? (
                  <div className="rounded-sm border border-line bg-surface-2/60 px-3 py-2 text-caption">{advanced.judge_spec.definition}</div>
                ) : null}
                {advanced.judge_spec.decision_steps?.length ? (
                  <ol className="list-decimal space-y-1 pl-5 text-caption text-text">
                    {advanced.judge_spec.decision_steps.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ol>
                ) : null}
                <JsonView value={advanced.judge_spec} title="judge_spec" defaultOpen maxHeight={480} />
              </>
            ) : (
              <div className="text-caption text-text-2">{t("checklists.editor.noSpec")}</div>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={regenerate} loading={regenerating} disabled={!advanced?.id}>
              <RefreshCw /> {t("checklists.editor.regenerate")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Save as new version */}
      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent>
          <DialogHeader className="flex-row items-center gap-3">
            <Mascot pose="typing" size={44} decorative className="-my-1" />
            <DialogTitle>{t("checklists.editor.saveAsVersion")}</DialogTitle>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>{t("checklists.editor.changeNote")}</Label>
              <Input value={changeNote} onChange={(e) => setChangeNote(e.target.value)} placeholder={t("checklists.editor.changeNotePlaceholder")} autoFocus />
            </div>
            <label className="flex items-center gap-2 text-caption">
              <Switch checked={compile} onCheckedChange={setCompile} /> {t("checklists.editor.compileSpecs")}
            </label>
            <Textarea readOnly value={criteria.map((c) => `${c.code} · ${c.title} · ${c.answer_type} · w${c.weight}${c.na_allowed ? " · N/A" : ""}${c.is_critical ? " · critical" : ""}`).join("\n")} className="min-h-[120px] font-mono text-[11px] text-text-2" />
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setSaveOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button variant="primary" onClick={save} loading={saving}>
              {t("common.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {checklist ? <DiffDialog checklist={checklist} open={diffOpen} onOpenChange={setDiffOpen} initialA={checklist.active_version?.id !== v.id ? checklist.active_version?.id : undefined} initialB={v.id} /> : null}

      <EvaluateDialog
        open={applyOpen}
        onOpenChange={setApplyOpen}
        conversationIds={null}
        defaultVersionId={v.id}
        defaultMode={checklist?.active_version && checklist.active_version.id !== v.id ? "partial" : "full"}
        defaultBaseVersionId={checklist?.active_version && checklist.active_version.id !== v.id ? checklist.active_version.id : null}
        title={t("checklists.applyDialog.title")}
        description={t("checklists.applyDialog.description")}
        onStarted={(jid) => setJobId(jid)}
      />
    </div>
  );
}
