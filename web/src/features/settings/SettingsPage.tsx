import * as React from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { useHealth, useSaveSettings, useSettings } from "@/api/queries";
import type { Settings } from "@/api/types";
import { apiBase } from "@/lib/config";
import { notify, notifyError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ErrorState, KeyValue, LoadingBlock, PageHeader, Section } from "@/components/common";

const EFFORTS = ["low", "medium", "high", "max"];

export default function SettingsPage() {
  const { t } = useTranslation();
  const settings = useSettings();
  const health = useHealth();
  const save = useSaveSettings();
  const [form, setForm] = React.useState<Partial<Settings>>({});

  React.useEffect(() => {
    if (settings.data) setForm(settings.data);
  }, [settings.data]);

  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setForm((f) => ({ ...f, [k]: v }));
  const dirty = settings.data ? JSON.stringify(pick(form)) !== JSON.stringify(pick(settings.data)) : false;

  const submit = async () => {
    try {
      await save.mutateAsync(pick(form));
      notify(t("settings.saved"));
      void health.refetch();
    } catch (e) {
      notifyError(e);
    }
  };

  if (settings.isLoading) return <LoadingBlock rows={8} className="p-6" />;
  if (settings.error) return <ErrorState error={settings.error} onRetry={() => settings.refetch()} className="m-6" />;

  return (
    <div className="flex flex-col gap-4 p-6">
      <PageHeader
        title={t("settings.title")}
        subtitle={t("settings.subtitle")}
        actions={
          <Button variant="primary" onClick={submit} loading={save.isPending} disabled={!dirty}>
            {t("common.save")}
          </Button>
        }
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_360px]">
        <div className="flex flex-col gap-4">
          <Section title={t("settings.judge")}>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Field label={t("settings.defaultProvider")}>
                <Select value={form.default_provider ?? "anthropic"} onValueChange={(v) => set("default_provider", v)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="anthropic">{t("provider.anthropic")}</SelectItem>
                    <SelectItem value="local">{t("provider.local")}</SelectItem>
                    <SelectItem value="ensemble">{t("provider.ensemble")}</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label={t("settings.judgeEffort")}>
                <Select value={form.judge_effort ?? "high"} onValueChange={(v) => set("judge_effort", v)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {EFFORTS.map((e) => (
                      <SelectItem key={e} value={e}>
                        {e}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label={t("settings.defaultModel")}>
                <Input value={form.default_model ?? ""} onChange={(e) => set("default_model", e.target.value)} className="font-mono" />
              </Field>
              <Field label={t("settings.secondaryModel")}>
                <Input value={form.secondary_model ?? ""} onChange={(e) => set("secondary_model", e.target.value)} className="font-mono" />
              </Field>
              <Field label={t("settings.explanationLanguage")}>
                <Select value={form.explanation_language ?? "ru"} onValueChange={(v) => set("explanation_language", v as Settings["explanation_language"])}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ru">RU · Русский</SelectItem>
                    <SelectItem value="en">EN · English</SelectItem>
                    <SelectItem value="kk">KK · Қазақша</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </div>
          </Section>

          <Section title={t("settings.local")}>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Field label={t("settings.localEndpoint")}>
                <Input value={form.local_endpoint ?? ""} onChange={(e) => set("local_endpoint", e.target.value || null)} placeholder="http://localhost:11434/v1" className="font-mono" />
              </Field>
              <Field label={t("settings.localModel")}>
                <Input value={form.local_model ?? ""} onChange={(e) => set("local_model", e.target.value)} placeholder="qwen3:8b" className="font-mono" />
              </Field>
            </div>
          </Section>

          <Section title={t("settings.privacy")}>
            <div className="flex flex-col gap-3">
              <label className="flex items-center justify-between gap-4 text-caption">
                <span>{t("settings.piiMasking")}</span>
                <Switch checked={!!form.pii_masking} onCheckedChange={(v) => set("pii_masking", v)} />
              </label>
              <label className="flex items-center justify-between gap-4 text-caption">
                <span>
                  {t("settings.fewshot")}
                  {settings.data?.fewshot_count != null ? <span className="ml-2 text-text-2">({t("settings.fewshotCount", { count: settings.data.fewshot_count })})</span> : null}
                </span>
                <Switch checked={!!form.fewshot_from_reviews} onCheckedChange={(v) => set("fewshot_from_reviews", v)} />
              </label>
            </div>
          </Section>
        </div>

        <div className="flex flex-col gap-4">
          <Section
            title={t("settings.health")}
            right={
              <Button size="sm" variant="ghost" onClick={() => health.refetch()}>
                <RefreshCw /> {t("settings.reload")}
              </Button>
            }
            bodyClassName="px-4 py-2"
          >
            <KeyValue k={t("settings.api")} v={<Status ok={!!health.data?.ok} label={health.data?.ok ? t("settings.ok") : t("settings.down")} />} />
            <KeyValue k="URL" v={<span className="font-mono text-[11px] text-text-2">{apiBase() || window.location.origin}</span>} />
            <KeyValue k={t("settings.db")} v={<span className="font-mono">{health.data?.db ?? "—"}</span>} />
            {Object.entries(health.data?.providers ?? {}).map(([k, v]) => (
              <KeyValue key={k} k={`${t("settings.providers")} · ${t(`provider.${k}` as never, { defaultValue: k })}`} v={<Status ok={v} label={v ? t("settings.configured") : t("settings.notConfigured")} />} />
            ))}
            {health.data?.organizer_api !== undefined ? <KeyValue k={t("settings.organizer")} v={<Status ok={health.data.organizer_api} label={health.data.organizer_api ? t("settings.configured") : t("settings.notConfigured")} />} /> : null}
            {health.error ? <ErrorState error={health.error} className="mt-2" /> : null}
          </Section>
          {settings.data?.cost_rates ? (
            <Section title={t("settings.costRates")} bodyClassName="px-4 py-2">
              {Object.entries(settings.data.cost_rates).map(([m, r]) => (
                <KeyValue key={m} k={<span className="font-mono">{m}</span>} v={<span className="font-mono">{(r as number[]).map((x) => `$${x}`).join(" / ")}</span>} />
              ))}
            </Section>
          ) : null}
          {settings.data?.ensemble_members?.length ? (
            <Section title={t("provider.ensemble")} bodyClassName="px-4 py-2">
              {settings.data.ensemble_members.map((m, i) => (
                <KeyValue key={i} k={m.provider} v={<span className="font-mono">{m.model}</span>} />
              ))}
            </Section>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function pick(s: Partial<Settings>): Partial<Settings> {
  return {
    default_provider: s.default_provider,
    default_model: s.default_model,
    secondary_model: s.secondary_model ?? null,
    explanation_language: s.explanation_language,
    pii_masking: s.pii_masking,
    fewshot_from_reviews: s.fewshot_from_reviews,
    local_endpoint: s.local_endpoint ?? null,
    local_model: s.local_model ?? null,
    judge_effort: s.judge_effort,
  };
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function Status({ ok, label }: { ok: boolean; label: string }) {
  return <Badge variant={ok ? "success" : "critical"}>{label}</Badge>;
}
