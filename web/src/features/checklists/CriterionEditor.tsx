import * as React from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, Braces, ChevronDown, ChevronRight, Plus, Trash2 } from "lucide-react";
import type { ChecklistSection, CriterionOption } from "@/api/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { maxPoints, type EditableCriterion } from "./model";

interface Props {
  c: EditableCriterion;
  index: number;
  count: number;
  sections: ChecklistSection[];
  onChange: (next: EditableCriterion) => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
  onAdvanced: () => void;
  defaultOpen?: boolean;
}

const BINARY_DEFAULT: CriterionOption[] = [
  { value: "yes", label: "Да", points: 1 },
  { value: "no", label: "Нет", points: 0 },
];
const SCALE_DEFAULT: CriterionOption[] = [
  { value: 10, label: "10", points: 10, anchor: "" },
  { value: 7, label: "7", points: 7, anchor: "" },
  { value: 4, label: "4", points: 4, anchor: "" },
  { value: 0, label: "0", points: 0, anchor: "" },
];

export function CriterionEditor({ c, index, count, sections, onChange, onMove, onRemove, onAdvanced, defaultOpen = false }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(defaultOpen);
  const set = <K extends keyof EditableCriterion>(k: K, v: EditableCriterion[K]) => onChange({ ...c, [k]: v });
  const setOption = (i: number, patch: Partial<CriterionOption>) => {
    const options = c.options.map((o, j) => (j === i ? { ...o, ...patch } : o));
    onChange({ ...c, options, max_points: c.is_critical && c.weight === 0 ? 0 : maxPoints(options) });
  };
  const voice = c.channel_overrides?.["voice call"]?.instruction ?? "";

  return (
    <div className={cn("card overflow-hidden", c.is_critical && "border-critical/40")}>
      <div className="flex items-center gap-2 px-3 py-2">
        <button type="button" className="text-text-2 hover:text-text" onClick={() => setOpen((o) => !o)} aria-label={open ? t("common.collapse") : t("common.expand")}>
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </button>
        <Input value={c.code} onChange={(e) => set("code", e.target.value.toUpperCase())} className="w-16 font-mono font-semibold" aria-label={t("checklists.editor.code")} />
        <Select value={c.section} onValueChange={(v) => set("section", v)}>
          <SelectTrigger className="w-24" aria-label={t("checklists.editor.section")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {sections.map((s) => (
              <SelectItem key={s.code} value={s.code}>
                {s.code} · {s.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input value={c.title} onChange={(e) => set("title", e.target.value)} className="flex-1 font-medium" placeholder={t("checklists.editor.titleCol")} aria-label={t("checklists.editor.titleCol")} />
        <Select
          value={c.answer_type}
          onValueChange={(v) => {
            const answer_type = v as "binary" | "scale";
            const options = answer_type === "binary" ? BINARY_DEFAULT.map((o) => ({ ...o })) : SCALE_DEFAULT.map((o) => ({ ...o }));
            onChange({ ...c, answer_type, options, max_points: c.is_critical && c.weight === 0 ? 0 : maxPoints(options) });
          }}
        >
          <SelectTrigger className="w-28" aria-label={t("checklists.editor.answerType")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="binary">{t("checklists.editor.binary")}</SelectItem>
            <SelectItem value="scale">{t("checklists.editor.scale")}</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex items-center gap-1">
          <span className="label">{t("checklists.editor.weight")}</span>
          <Input type="number" min={0} step={1} value={c.weight} onChange={(e) => set("weight", Number(e.target.value))} className="w-16 font-mono" aria-label={t("checklists.editor.weight")} />
        </div>
        {c.is_critical ? <Badge variant="critical">C!</Badge> : null}
        {c.na_allowed ? <Badge variant="outline">N/A</Badge> : null}
        <div className="ml-1 flex items-center">
          <Button size="icon-sm" variant="ghost" onClick={() => onMove(-1)} disabled={index === 0} aria-label={t("checklists.editor.moveUp")}>
            <ArrowUp />
          </Button>
          <Button size="icon-sm" variant="ghost" onClick={() => onMove(1)} disabled={index === count - 1} aria-label={t("checklists.editor.moveDown")}>
            <ArrowDown />
          </Button>
          <Button size="icon-sm" variant="ghost" onClick={onAdvanced} aria-label={t("checklists.editor.advanced")} title={t("checklists.editor.advanced")}>
            <Braces />
          </Button>
          <Button size="icon-sm" variant="ghost" className="text-critical" onClick={onRemove} aria-label={t("checklists.editor.remove")}>
            <Trash2 />
          </Button>
        </div>
      </div>
      {open ? (
        <div className="grid grid-cols-1 gap-4 border-t border-line p-4 lg:grid-cols-2">
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>{t("checklists.editor.instruction")}</Label>
              <Textarea value={c.instruction} onChange={(e) => set("instruction", e.target.value)} className="min-h-[120px]" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("checklists.editor.voiceOverride")}</Label>
              <Textarea value={voice} onChange={(e) => set("channel_overrides", { ...c.channel_overrides, "voice call": { instruction: e.target.value } })} className="min-h-[72px]" placeholder="—" />
            </div>
            <div className="flex flex-wrap items-center gap-5">
              <label className="flex items-center gap-2 text-caption">
                <Switch checked={c.na_allowed} onCheckedChange={(v) => set("na_allowed", v)} /> {t("checklists.editor.naAllowed")}
              </label>
              <label className="flex items-center gap-2 text-caption">
                <Switch
                  checked={c.is_critical}
                  onCheckedChange={(v) => onChange({ ...c, is_critical: v, critical_value: v ? c.critical_value ?? "yes" : null, weight: v ? 0 : c.weight || 10, max_points: v ? 0 : maxPoints(c.options) })}
                />
                {t("checklists.editor.isCritical")}
              </label>
            </div>
            {c.na_allowed ? (
              <div className="flex flex-col gap-1.5">
                <Label>{t("checklists.editor.naCondition")}</Label>
                <Textarea value={c.na_condition ?? ""} onChange={(e) => set("na_condition", e.target.value)} className="min-h-[56px]" />
              </div>
            ) : null}
          </div>
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <Label>{t("checklists.editor.options")}</Label>
              <span className="num font-mono text-[11px] text-text-2">
                {t("checklists.editor.maxPoints")}: {c.is_critical && c.weight === 0 ? 0 : maxPoints(c.options)}
              </span>
            </div>
            <div className="flex flex-col gap-1.5">
              <div className="grid grid-cols-[72px_96px_72px_1fr_28px] gap-1.5 px-1 text-[11px] uppercase tracking-[0.04em] text-text-2">
                <span>{t("checklists.editor.optionValue")}</span>
                <span>{t("checklists.editor.optionLabel")}</span>
                <span>{t("checklists.editor.optionPoints")}</span>
                <span>{t("checklists.editor.optionAnchor")}</span>
                <span />
              </div>
              {c.options.map((o, i) => (
                <div key={i} className="grid grid-cols-[72px_96px_72px_1fr_28px] items-center gap-1.5">
                  <Input value={String(o.value)} onChange={(e) => setOption(i, { value: c.answer_type === "scale" ? Number(e.target.value) || 0 : e.target.value })} className="font-mono" />
                  <Input value={o.label} onChange={(e) => setOption(i, { label: e.target.value })} />
                  <Input type="number" value={o.points} onChange={(e) => setOption(i, { points: Number(e.target.value) })} className="font-mono" />
                  <Input value={o.anchor ?? ""} onChange={(e) => setOption(i, { anchor: e.target.value })} placeholder="—" />
                  <Button size="icon-sm" variant="ghost" className="text-critical" onClick={() => onChange({ ...c, options: c.options.filter((_, j) => j !== i) })} disabled={c.options.length <= 2}>
                    <Trash2 />
                  </Button>
                </div>
              ))}
              <Button size="sm" variant="ghost" className="w-fit" onClick={() => onChange({ ...c, options: [...c.options, { value: c.answer_type === "scale" ? 0 : "", label: "", points: 0, anchor: "" }] })}>
                <Plus /> {t("checklists.editor.addOption")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
