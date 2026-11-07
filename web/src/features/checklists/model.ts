import type { ChecklistBody, ChecklistVersion, Criterion, CriterionOption } from "@/api/types";

export type EditableCriterion = Omit<Criterion, "id" | "judge_spec"> & { _key: string; id?: number; judge_spec?: Criterion["judge_spec"] };

let seq = 0;
export const newKey = () => `k${Date.now().toString(36)}${(seq++).toString(36)}`;

export function toEditable(v: ChecklistVersion): EditableCriterion[] {
  return [...v.criteria]
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
    .map((c) => ({ ...structuredClone(c), _key: newKey() }));
}

export function blankCriterion(code: string, section: string): EditableCriterion {
  return {
    _key: newKey(),
    code,
    section,
    title: "",
    answer_type: "binary",
    options: [
      { value: "yes", label: "Да", points: 1 },
      { value: "no", label: "Нет", points: 0 },
    ],
    max_points: 1,
    weight: 10,
    na_allowed: false,
    na_condition: null,
    is_critical: false,
    critical_value: null,
    instruction: "",
    channel_overrides: {},
  };
}

export function maxPoints(options: CriterionOption[]): number {
  return options.reduce((m, o) => Math.max(m, Number(o.points) || 0), 0);
}

export function stripCriterion(c: EditableCriterion, order: number): ChecklistBody["criteria"][number] {
  const { _key, id, judge_spec, ...rest } = c;
  void _key;
  void id;
  void judge_spec;
  const options = rest.options.map((o) => ({
    value: rest.answer_type === "scale" ? Number(o.value) : String(o.value),
    label: String(o.label ?? o.value),
    points: Number(o.points) || 0,
    ...(o.anchor ? { anchor: o.anchor } : {}),
  }));
  return {
    ...rest,
    order,
    options,
    max_points: rest.is_critical && rest.weight === 0 ? 0 : maxPoints(options),
    weight: Number(rest.weight) || 0,
    na_condition: rest.na_allowed ? rest.na_condition || null : null,
    critical_value: rest.is_critical ? rest.critical_value ?? "yes" : null,
    channel_overrides: Object.fromEntries(Object.entries(rest.channel_overrides ?? {}).filter(([, v]) => v && v.instruction && v.instruction.trim())),
  };
}

export function toChecklistBody(v: ChecklistVersion, criteria?: EditableCriterion[]): ChecklistBody {
  const list = criteria ?? toEditable(v);
  return {
    scoring_rules: v.scoring_rules,
    sections: v.sections,
    criteria: list.map((c, i) => stripCriterion(c, i)),
  };
}

export function exportVersionJson(v: ChecklistVersion, criteria: EditableCriterion[], name?: string): string {
  const body = toChecklistBody(v, criteria);
  return JSON.stringify({ name, version_no: v.version_no, change_note: v.change_note, ...body, criteria: body.criteria.map((c, i) => ({ ...c, judge_spec: criteria[i].judge_spec ?? null })) }, null, 2);
}

export function criteriaEqual(a: EditableCriterion[], b: EditableCriterion[]): boolean {
  if (a.length !== b.length) return false;
  return JSON.stringify(a.map((c, i) => stripCriterion(c, i))) === JSON.stringify(b.map((c, i) => stripCriterion(c, i)));
}
