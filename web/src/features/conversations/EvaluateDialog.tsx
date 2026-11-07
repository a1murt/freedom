import * as React from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/api/endpoints";
import type { Provider, RunBody, RunResult } from "@/api/types";
import { useChecklists, useSettings } from "@/api/queries";
import { fmtMoney } from "@/lib/format";
import { notify, notifyError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

export interface EvaluateDialogProps {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  /** Conversation ids to evaluate; null or empty means "all". */
  conversationIds: string[] | null;
  allowScopeAll?: boolean;
  defaultVersionId?: number | null;
  defaultMode?: "full" | "partial";
  defaultBaseVersionId?: number | null;
  title?: string;
  description?: string;
  onStarted: (jobId: string, result: RunResult) => void;
}

export function EvaluateDialog({ open, onOpenChange, conversationIds, allowScopeAll = true, defaultVersionId, defaultMode = "full", defaultBaseVersionId, title, description, onStarted }: EvaluateDialogProps) {
  const { t } = useTranslation();
  const checklists = useChecklists();
  const settings = useSettings();
  const versions = React.useMemo(
    () => (checklists.data ?? []).flatMap((c) => c.versions.map((v) => ({ ...v, checklistName: c.name, checklistId: c.id }))),
    [checklists.data],
  );
  const activeVersion = React.useMemo(() => versions.find((v) => v.is_active) ?? versions[0], [versions]);

  const [scopeAll, setScopeAll] = React.useState(!conversationIds || conversationIds.length === 0);
  const [versionId, setVersionId] = React.useState<string>("");
  const [provider, setProvider] = React.useState<Provider>("anthropic");
  const [mode, setMode] = React.useState<"full" | "partial">(defaultMode);
  const [baseVersionId, setBaseVersionId] = React.useState<string>(defaultBaseVersionId ? String(defaultBaseVersionId) : "");
  const [model, setModel] = React.useState("");
  const [estimate, setEstimate] = React.useState<RunResult | null>(null);
  const [busy, setBusy] = React.useState<"dry" | "run" | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setScopeAll(!conversationIds || conversationIds.length === 0);
    setMode(defaultMode);
    setBaseVersionId(defaultBaseVersionId ? String(defaultBaseVersionId) : "");
    setEstimate(null);
    setBusy(null);
  }, [open, conversationIds, defaultMode, defaultBaseVersionId]);

  React.useEffect(() => {
    if (!open) return;
    if (defaultVersionId) setVersionId(String(defaultVersionId));
    else if (activeVersion && !versionId) setVersionId(String(activeVersion.id));
  }, [open, defaultVersionId, activeVersion, versionId]);

  React.useEffect(() => {
    if (settings.data?.default_provider && open) setProvider(settings.data.default_provider);
  }, [settings.data?.default_provider, open]);

  React.useEffect(() => setEstimate(null), [scopeAll, versionId, provider, mode, baseVersionId, model]);

  const body = (dry: boolean): RunBody => ({
    conversation_ids: scopeAll ? null : conversationIds,
    all: scopeAll,
    checklist_version_id: versionId ? Number(versionId) : null,
    provider,
    model: model.trim() || null,
    mode,
    base_version_id: mode === "partial" && baseVersionId ? Number(baseVersionId) : null,
    dry_run: dry,
  });

  const dryRun = async () => {
    setBusy("dry");
    try {
      setEstimate(await api.run(body(true)));
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(null);
    }
  };

  const run = async () => {
    setBusy("run");
    try {
      const res = await api.run(body(false));
      if (!res.job_id) throw new Error("no job_id in response");
      notify(t("conversations.evaluateDialog.started", { id: res.job_id }), `${t("conversations.evaluateDialog.queued", { count: res.queued })} · ${fmtMoney(res.estimated_cost_usd)}`);
      onStarted(res.job_id, res);
      onOpenChange(false);
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(null);
    }
  };

  const selectedCount = conversationIds?.length ?? 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title ?? t("conversations.evaluateDialog.title")}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          {allowScopeAll && selectedCount > 0 ? (
            <div className="flex flex-col gap-1.5">
              <Label>{t("conversations.evaluateDialog.scope")}</Label>
              <div className="inline-flex h-8 w-fit items-center rounded-sm border border-line bg-surface-2 p-0.5">
                <button type="button" onClick={() => setScopeAll(false)} className={`h-full rounded-[4px] px-2.5 text-caption font-medium ${!scopeAll ? "bg-surface text-text shadow-sm" : "text-text-2"}`}>
                  {t("conversations.evaluateDialog.scopeSelected", { count: selectedCount })}
                </button>
                <button type="button" onClick={() => setScopeAll(true)} className={`h-full rounded-[4px] px-2.5 text-caption font-medium ${scopeAll ? "bg-surface text-text shadow-sm" : "text-text-2"}`}>
                  {t("conversations.evaluateDialog.scopeAll")}
                </button>
              </div>
            </div>
          ) : null}

          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2 flex flex-col gap-1.5">
              <Label>{t("conversations.evaluateDialog.checklist")}</Label>
              <Select value={versionId} onValueChange={setVersionId}>
                <SelectTrigger>
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  {versions.map((v) => (
                    <SelectItem key={v.id} value={String(v.id)}>
                      v{v.version_no} · {v.change_note || v.checklistName} {v.is_active ? `· ${t("common.active").toLowerCase()}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("conversations.evaluateDialog.provider")}</Label>
              <Select value={provider} onValueChange={(v) => setProvider(v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="anthropic">{t("provider.anthropic")}</SelectItem>
                  <SelectItem value="local">{t("provider.local")}</SelectItem>
                  <SelectItem value="ensemble">{t("provider.ensemble")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("conversations.evaluateDialog.model")}</Label>
              <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder={provider === "local" ? settings.data?.local_model ?? "" : settings.data?.default_model ?? ""} className="font-mono" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("conversations.evaluateDialog.mode")}</Label>
              <Select value={mode} onValueChange={(v) => setMode(v as "full" | "partial")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="full">{t("conversations.evaluateDialog.modeFull")}</SelectItem>
                  <SelectItem value="partial">{t("conversations.evaluateDialog.modePartial")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {mode === "partial" ? (
              <div className="flex flex-col gap-1.5">
                <Label>{t("conversations.evaluateDialog.baseVersion")}</Label>
                <Select value={baseVersionId} onValueChange={setBaseVersionId}>
                  <SelectTrigger>
                    <SelectValue placeholder="—" />
                  </SelectTrigger>
                  <SelectContent>
                    {versions
                      .filter((v) => String(v.id) !== versionId)
                      .map((v) => (
                        <SelectItem key={v.id} value={String(v.id)}>
                          v{v.version_no} · {v.change_note || v.checklistName}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}
          </div>

          <div className="flex min-h-[44px] items-center justify-between rounded-sm border border-line bg-surface-2/60 px-3 py-2 text-caption">
            <span className="text-text-2">{t("conversations.evaluateDialog.estimate")}</span>
            {estimate ? (
              <div className="flex flex-wrap items-center justify-end gap-2">
                <span className="num font-mono text-body font-medium text-text">{fmtMoney(estimate.estimated_cost_usd)}</span>
                <Badge variant="outline">{t("conversations.evaluateDialog.queued", { count: estimate.queued })}</Badge>
                {estimate.rejudge_codes && estimate.rejudge_codes.length ? <Badge variant="accent">{t("conversations.evaluateDialog.rejudge", { codes: estimate.rejudge_codes.join(", ") })}</Badge> : null}
              </div>
            ) : (
              <span className="text-text-2">—</span>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button variant="secondary" onClick={dryRun} loading={busy === "dry"} disabled={!versionId || busy === "run"}>
            {t("common.dryRun")}
          </Button>
          <Button variant="primary" onClick={run} loading={busy === "run"} disabled={!versionId || busy === "dry"}>
            {t("common.run")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
