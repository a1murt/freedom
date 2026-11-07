/* In-memory mock of the CQA API (docs/API.md) built from fixtures captured from the real backend.
 * Enabled with VITE_MOCK=1 (or ?mock=1). State mutations (appeals, decisions, versions, settings,
 * jobs) live for the page lifetime only. */
import conversationsFx from "./fixtures/conversations.json";
import detailsFx from "./fixtures/conversation_details.json";
import latestFx from "./fixtures/latest_evaluations.json";
import historyRu029 from "./fixtures/evaluation_history_p1-ru-029.json";
import evaluation1 from "./fixtures/evaluation_1.json";
import trace31 from "./fixtures/trace_31.json";
import audioFeatures from "./fixtures/audio_features_p4-ru-001.json";
import checklistsFx from "./fixtures/checklists.json";
import version2 from "./fixtures/version_2.json";
import diff12 from "./fixtures/diff_1_2.json";
import appealsFx from "./fixtures/appeals.json";
import analyticsFx from "./fixtures/analytics.json";
import settingsFx from "./fixtures/settings.json";
import healthFx from "./fixtures/health.json";
import type {
  Appeal,
  AppealWithContext,
  Checklist,
  ChecklistVersion,
  ConversationDetail,
  ConversationSummary,
  Criterion,
  Evaluation,
  EvaluationCriterion,
  Job,
  Settings,
  Turn,
} from "@/api/types";

type Json = Record<string, unknown> | unknown[] | null;

const state = {
  conversations: structuredClone(conversationsFx.items) as ConversationSummary[],
  details: structuredClone(detailsFx) as Record<string, ConversationDetail>,
  latest: structuredClone(latestFx) as Record<string, Evaluation>,
  evaluationsById: new Map<number, Evaluation>(),
  checklists: structuredClone(checklistsFx) as unknown as Checklist[],
  versions: new Map<number, ChecklistVersion>(),
  appeals: structuredClone(appealsFx) as unknown as AppealWithContext[],
  settings: structuredClone(settingsFx) as unknown as Settings,
  jobs: new Map<string, Job & { _tick: number }>(),
  nextAppealId: 100,
  nextVersionId: 100,
  nextEvalId: 1000,
};

for (const e of Object.values(state.latest)) state.evaluationsById.set(e.id, e);
state.evaluationsById.set((evaluation1 as unknown as Evaluation).id, evaluation1 as unknown as Evaluation);
for (const c of state.checklists) {
  if (c.active_version) state.versions.set(c.active_version.id, c.active_version);
}
state.versions.set((version2 as unknown as ChecklistVersion).id, version2 as unknown as ChecklistVersion);

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

function json(data: Json, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}
function error(status: number, code: string, message: string): Response {
  return json({ error: { code, message } }, status);
}

function synthDetail(s: ConversationSummary): ConversationDetail {
  const isCall = s.channel === "voice call";
  const greet =
    s.language === "KZ" ? "Сәлеметсіз бе! Менің атым " : s.language === "EN" ? "Hello! My name is " : "Здравствуйте! Меня зовут ";
  const turns: Turn[] = [
    { idx: 0, timestamp_raw: "00:00", offset_seconds: 0, speaker: "client", text: "…", is_duplicate: false, segment: "pre" },
    { idx: 1, timestamp_raw: "00:02", offset_seconds: 2, speaker: "bot", text: "Здравствуйте! Я AIM, AI-ассистент Freedom Broker.", is_duplicate: false, segment: "pre" },
    { idx: 2, timestamp_raw: "00:40", offset_seconds: 40, speaker: "system", text: "Эскалация на оператора.", is_duplicate: false, segment: "pre" },
    { idx: 3, timestamp_raw: "01:10", offset_seconds: 70, speaker: "operator", text: `${greet}${s.operator_name ?? "[Имя скрыто]"}.`, is_duplicate: false, segment: "operator" },
    { idx: 4, timestamp_raw: "01:30", offset_seconds: 90, speaker: "client", text: "…", is_duplicate: false, segment: "operator" },
  ];
  return { ...s, turns, audio_url_available: isCall, meta: { operator_start_idx: 3, escalation_idx: 2 } };
}

function listConversations(url: URL): Response {
  const p = url.searchParams;
  let items = state.conversations.slice();
  const channel = p.get("channel");
  const language = p.get("language");
  const status = p.get("status");
  const q = p.get("q")?.toLowerCase();
  const minT = p.get("min_total");
  const maxT = p.get("max_total");
  const critical = p.get("critical");
  if (channel) items = items.filter((c) => c.channel === channel);
  if (language) items = items.filter((c) => c.language === language);
  if (status === "evaluated") items = items.filter((c) => !!c.latest_evaluation);
  if (status === "not_evaluated") items = items.filter((c) => !c.latest_evaluation);
  if (status === "under_review") items = items.filter((c) => c.review_state === "under_review");
  if (minT) items = items.filter((c) => (c.latest_evaluation?.final_total_score ?? -1) >= Number(minT));
  if (maxT) items = items.filter((c) => (c.latest_evaluation?.final_total_score ?? 101) <= Number(maxT));
  if (critical === "true") items = items.filter((c) => c.latest_evaluation?.critical_error);
  if (q) items = items.filter((c) => c.id.toLowerCase().includes(q) || (c.operator_name ?? "").toLowerCase().includes(q));
  return json({ items, total: items.length });
}

function startJob(ids: string[], params: Record<string, unknown>): Job {
  const id = `j_mock_${Math.random().toString(16).slice(2, 10)}`;
  const job: Job & { _tick: number } = {
    id,
    type: "evaluate",
    status: "running",
    total: ids.length,
    done: 0,
    failed: 0,
    cost_usd: 0,
    params: { ...params, conversation_ids: ids },
    started_at: new Date().toISOString(),
    finished_at: null,
    created_at: new Date().toISOString(),
    log: [{ ts: new Date().toISOString(), level: "info", message: `queued ${ids.length} conversations (mock)` }],
    _tick: 0,
  };
  state.jobs.set(id, job);
  return job;
}

function tickJob(job: Job & { _tick: number }) {
  if (job.status !== "running") return;
  job._tick += 1;
  const step = Math.max(1, Math.ceil(job.total / 6));
  const ids = (job.params?.conversation_ids as string[]) ?? [];
  const before = job.done;
  job.done = Math.min(job.total, job.done + step);
  for (let i = before; i < job.done; i++) {
    const cid = ids[i];
    const total = Math.round((55 + Math.random() * 45) * 100) / 100;
    job.cost_usd = Math.round((job.cost_usd + 0.19) * 10000) / 10000;
    job.log.push({ ts: new Date().toISOString(), level: "info", message: `${cid}: total=${total} critical=False cost=$0.19 (mock)` });
    const summary = state.conversations.find((c) => c.id === cid);
    if (summary) {
      const template = state.latest["p1-ru-006"] ?? Object.values(state.latest)[0];
      const ev: Evaluation = {
        ...structuredClone(template),
        id: state.nextEvalId++,
        conversation_id: cid,
        checklist_version_id: Number(job.params?.checklist_version_id ?? 1),
        provider: String(job.params?.provider ?? "anthropic"),
        mode: job.params?.provider === "ensemble" ? "ensemble" : "single",
        total_score: total,
        final_total_score: total,
        critical_error: false,
        created_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        review_state: "none",
      };
      state.latest[cid] = ev;
      state.evaluationsById.set(ev.id, ev);
      summary.latest_evaluation = {
        id: ev.id,
        status: "completed",
        total_score: total,
        final_total_score: total,
        critical_error: false,
        provider: ev.provider,
        mode: ev.mode,
        model: ev.model,
        cost_usd: 0.19,
        checklist_version_id: ev.checklist_version_id,
        completed_at: ev.completed_at,
      };
    }
  }
  if (job.done >= job.total) {
    job.status = "completed";
    job.finished_at = new Date().toISOString();
    job.log.push({ ts: new Date().toISOString(), level: "info", message: "job completed (mock)" });
  }
}

function findCriterion(ecId: number): { ev: Evaluation; crit: EvaluationCriterion } | null {
  for (const ev of state.evaluationsById.values()) {
    const crit = ev.criteria?.find((c) => c.id === ecId);
    if (crit) return { ev, crit };
  }
  return null;
}

function recalc(ev: Evaluation) {
  const crits = ev.criteria ?? [];
  let total = 0;
  for (const c of crits) {
    if (!c.applicable) continue;
    const score = c.review_state === "changed" && c.final_score !== null ? c.final_score : c.score ?? 0;
    const earned = c.max_points > 0 ? (score / c.max_points) * c.weight_applied : 0;
    c.final_earned = Math.round(earned * 10000) / 10000;
    total += earned;
  }
  ev.final_total_score = Math.round(total * 100) / 100;
  const s = state.conversations.find((c) => c.id === ev.conversation_id);
  if (s?.latest_evaluation && s.latest_evaluation.id === ev.id) s.latest_evaluation.final_total_score = ev.final_total_score;
}

function previewTotal(ev: Evaluation, ecId: number, newAnswer: string | number | null): number {
  const copy = structuredClone(ev);
  const crit = copy.criteria?.find((c) => c.id === ecId);
  if (crit) {
    const opt = crit.options?.find((o) => String(o.value) === String(newAnswer));
    crit.review_state = "changed";
    crit.final_score = newAnswer === null ? 0 : opt?.points ?? 0;
    if (newAnswer === null) crit.applicable = false;
  }
  recalc(copy);
  return copy.final_total_score ?? 0;
}

export async function mockFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  await delay(120 + Math.random() * 180);
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url, window.location.origin);
  const method = (init?.method ?? "GET").toUpperCase();
  const path = url.pathname.replace(/\/+$/, "");
  const body = init?.body && typeof init.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
  const seg = path.split("/").filter(Boolean); // ["api", ...]
  const r = seg.slice(1);

  // --- health / settings / analytics
  if (path === "/api/health") return json(healthFx as unknown as Json);
  if (path === "/api/settings") {
    if (method === "PUT" && body) Object.assign(state.settings, body);
    return json(state.settings as unknown as Json);
  }
  if (path === "/api/analytics/summary") return json(analyticsFx as unknown as Json);

  // --- conversations
  if (path === "/api/conversations" && method === "GET") return listConversations(url);
  if (path === "/api/datasets/import") {
    return json({ dataset_id: 1, imported: state.conversations.length, by_language: { RU: 35, KZ: 15, EN: 15 }, by_channel: { "agent chat": 60, "voice call": 5 } });
  }
  if (path === "/api/conversations/paste" || path === "/api/conversations/upload") {
    const n = state.conversations.filter((c) => c.id.startsWith("manual-")).length + 1;
    const id = (body?.conversation_id as string) || `manual-${String(n).padStart(3, "0")}`;
    const text = (body?.text as string) ?? "client\tЗдравствуйте, подскажите тариф.\noperator\tЗдравствуйте! Меня зовут Алина. Тариф указан в разделе «Кабинет».";
    const turns: Turn[] = text
      .split(/\r?\n/)
      .filter((l) => l.trim())
      .map((l, i) => {
        const [sp, ...rest] = l.split(/\t|:\s/);
        const speaker = (["client", "operator", "bot", "system"].includes(sp.trim().toLowerCase()) ? sp.trim().toLowerCase() : "client") as Turn["speaker"];
        return { idx: i, timestamp_raw: `00:${String(i * 7).padStart(2, "0")}`, offset_seconds: i * 7, speaker, text: rest.join(" ").trim() || l, is_duplicate: false, segment: "operator" as const };
      });
    const summary: ConversationSummary = {
      id,
      channel: (body?.channel as string) ?? "agent chat",
      language: (body?.language as string) ?? "RU",
      operator_name: null,
      turn_count: turns.length,
      has_audio: false,
      first_response_seconds: 7,
      latest_evaluation: null,
      review_state: "none",
      imported_at: new Date().toISOString(),
    };
    const detail: ConversationDetail = { ...summary, turns, audio_url_available: false };
    state.conversations.unshift(summary);
    state.details[id] = detail;
    return json(detail as unknown as Json);
  }
  if (r[0] === "conversations" && r[1]) {
    const id = decodeURIComponent(r[1]);
    const summary = state.conversations.find((c) => c.id === id);
    if (!summary) return error(404, "not_found", "conversation not found");
    if (r.length === 2) {
      const d = state.details[id] ?? synthDetail(summary);
      return json({ ...d, latest_evaluation: summary.latest_evaluation, review_state: summary.review_state } as unknown as Json);
    }
    if (r[2] === "evaluation") {
      const ev = state.latest[id];
      if (!ev) return error(404, "not_found", "no evaluation yet");
      return json(ev as unknown as Json);
    }
    if (r[2] === "evaluations") {
      if (id === "p1-ru-029") {
        const hist = structuredClone(historyRu029) as unknown as Evaluation[];
        hist[0] = { ...hist[0], ...state.latest[id], criteria: undefined };
        return json(hist as unknown as Json);
      }
      const ev = state.latest[id];
      return json(ev ? [{ ...ev, criteria: undefined }] : []);
    }
  }
  if (r[0] === "audio" && r[2] === "features") {
    const id = decodeURIComponent(r[1]);
    const s = state.conversations.find((c) => c.id === id);
    if (!s?.has_audio) return error(404, "not_found", "no audio for this conversation");
    return json(audioFeatures as unknown as Json);
  }
  if (r[0] === "audio") return error(404, "not_found", "audio is not available in mock mode");

  // --- evaluations / jobs
  if (path === "/api/evaluations/run" && body) {
    const ids = body.all ? state.conversations.map((c) => c.id) : ((body.conversation_ids as string[]) ?? []);
    if (ids.length === 0) return error(400, "no_conversations", "nothing to evaluate");
    const version = state.versions.get(Number(body.checklist_version_id ?? 1));
    const nCrit = version?.criteria.length ?? 8;
    let codes: string[] | null = null;
    if (body.mode === "partial" && body.base_version_id) codes = (diff12 as { rejudge_codes?: string[] }).rejudge_codes ?? ["C1"];
    const per = body.provider === "ensemble" ? 0.48 : body.provider === "local" ? 0 : 0.19;
    let est = Math.round(ids.length * per * 100) / 100;
    if (codes) est = Math.round((est * codes.length) / nCrit * 100) / 100;
    if (body.dry_run) return json({ queued: ids.length, estimated_cost_usd: est, rejudge_codes: codes, checklist_version_id: version?.id ?? 1 });
    const job = startJob(ids, { checklist_version_id: version?.id ?? 1, provider: body.provider, mode: body.mode, base_version_id: body.base_version_id ?? null });
    return json({ job_id: job.id, queued: ids.length, estimated_cost_usd: est, rejudge_codes: codes, checklist_version_id: version?.id ?? 1 });
  }
  if (r[0] === "jobs" && r[1] && !r[2]) {
    const job = state.jobs.get(r[1]);
    if (!job) return error(404, "not_found", "job not found");
    tickJob(job);
    const { _tick, ...rest } = job;
    void _tick;
    return json(rest as unknown as Json);
  }
  if (r[0] === "jobs" && r[2] === "cancel") {
    const job = state.jobs.get(r[1]);
    if (!job) return error(404, "not_found", "job not running");
    job.status = "cancelled";
    job.finished_at = new Date().toISOString();
    return json(job as unknown as Json);
  }
  if (r[0] === "jobs" && !r[1]) return json([...state.jobs.values()] as unknown as Json);
  if (r[0] === "evaluations" && r[1] && r[2] === "trace") {
    return json(trace31 as unknown as Json);
  }
  if (r[0] === "evaluations" && r[1] && !r[2]) {
    const ev = state.evaluationsById.get(Number(r[1]));
    if (!ev) return error(404, "not_found", "evaluation not found");
    return json(ev as unknown as Json);
  }

  // --- checklists
  if (path === "/api/checklists" && method === "GET") return json(state.checklists as unknown as Json);
  if (path === "/api/checklists" && method === "POST" && body) {
    const v: ChecklistVersion = {
      id: state.nextVersionId++,
      checklist_id: state.checklists.length + 1,
      version_no: 1,
      is_active: false,
      change_note: (body.change_note as string) ?? "v1",
      scoring_rules: (body.scoring_rules as ChecklistVersion["scoring_rules"]) ?? {},
      sections: (body.sections as ChecklistVersion["sections"]) ?? [],
      criteria: ((body.criteria as Criterion[]) ?? []).map((c, i) => ({ ...c, id: 1000 + i, order: i })),
      created_at: new Date().toISOString(),
    };
    state.versions.set(v.id, v);
    state.checklists.push({ id: v.checklist_id, name: (body.name as string) ?? "New checklist", description: (body.description as string) ?? "", active_version: v, versions: [{ id: v.id, version_no: 1, created_at: v.created_at!, change_note: v.change_note, is_active: false }] });
    return json(v as unknown as Json);
  }
  if (r[0] === "checklists" && r[1]) {
    const cl = state.checklists.find((c) => c.id === Number(r[1]));
    if (!cl) return error(404, "not_found", "checklist not found");
    if (r[2] === "versions" && !r[3] && method === "POST" && body) {
      const base = cl.active_version;
      const v: ChecklistVersion = {
        id: state.nextVersionId++,
        checklist_id: cl.id,
        version_no: cl.versions.length + 1,
        is_active: false,
        change_note: (body.change_note as string) ?? "",
        scoring_rules: (body.scoring_rules as ChecklistVersion["scoring_rules"]) ?? base?.scoring_rules ?? {},
        sections: (body.sections as ChecklistVersion["sections"]) ?? base?.sections ?? [],
        criteria: ((body.criteria as Criterion[]) ?? []).map((c, i) => ({
          ...c,
          id: state.nextVersionId * 100 + i,
          order: i,
          judge_spec: base?.criteria.find((b) => b.code === c.code)?.judge_spec ?? { definition: `(mock) regenerated spec for ${c.code}`, decision_steps: [], na_conditions: [] },
        })),
        created_at: new Date().toISOString(),
        created_from_version_id: base?.id ?? null,
      };
      state.versions.set(v.id, v);
      cl.versions.push({ id: v.id, version_no: v.version_no, created_at: v.created_at!, change_note: v.change_note, is_active: false });
      return json(v as unknown as Json);
    }
    if (r[2] === "versions" && r[3] && r[4] === "diff" && r[5]) {
      const a = state.versions.get(Number(r[3]));
      const b = state.versions.get(Number(r[5]));
      if (!a || !b) return error(404, "not_found", "version not found");
      const codesA = new Set(a.criteria.map((c) => c.code));
      const codesB = new Set(b.criteria.map((c) => c.code));
      const added = [...codesB].filter((c) => !codesA.has(c));
      const removed = [...codesA].filter((c) => !codesB.has(c));
      const changed: { code: string; fields: string[]; requires_rejudge: boolean }[] = [];
      const judged = new Set(["instruction", "answer_type", "options", "na_allowed", "na_condition", "channel_overrides", "title", "is_critical"]);
      for (const ca of a.criteria) {
        const cb = b.criteria.find((c) => c.code === ca.code);
        if (!cb) continue;
        const fields: string[] = [];
        for (const k of ["title", "section", "instruction", "answer_type", "options", "weight", "na_allowed", "na_condition", "is_critical", "channel_overrides"] as const) {
          if (JSON.stringify(ca[k] ?? null) !== JSON.stringify(cb[k] ?? null)) fields.push(k);
        }
        if (fields.length) changed.push({ code: ca.code, fields, requires_rejudge: fields.some((f) => judged.has(f)) });
      }
      return json({ from_version_id: a.id, to_version_id: b.id, added, removed, changed, scoring_rules_changed: JSON.stringify(a.scoring_rules) !== JSON.stringify(b.scoring_rules), rejudge_codes: [...added, ...changed.filter((c) => c.requires_rejudge).map((c) => c.code)] });
    }
    if (r[2] === "versions" && r[3] && r[4] === "activate") {
      const v = state.versions.get(Number(r[3]));
      if (!v) return error(404, "not_found", "version not found");
      for (const vv of state.versions.values()) if (vv.checklist_id === cl.id) vv.is_active = false;
      v.is_active = true;
      cl.active_version = v;
      cl.versions = cl.versions.map((x) => ({ ...x, is_active: x.id === v.id }));
      return json(v as unknown as Json);
    }
    if (r[2] === "versions" && r[3] && r[4] === "judge-spec" && r[6] === "regenerate") {
      const v = state.versions.get(Number(r[3]));
      const c = v?.criteria.find((x) => x.code === decodeURIComponent(r[5]));
      if (!c) return error(404, "not_found", "criterion not found");
      c.judge_spec = { ...(c.judge_spec ?? {}), definition: `(mock, regenerated ${new Date().toLocaleTimeString()}) ${c.judge_spec?.definition ?? c.instruction}` };
      return json(c as unknown as Json);
    }
    if (r[2] === "versions" && r[3]) {
      const v = state.versions.get(Number(r[3]));
      if (!v) return error(404, "not_found", "version not found");
      return json(v as unknown as Json);
    }
    return json(cl as unknown as Json);
  }

  // --- appeals
  if (path === "/api/appeals" && method === "GET") {
    const st = url.searchParams.get("status");
    let items = state.appeals.slice();
    if (st) items = items.filter((a) => a.status === st);
    items.sort((a, b) => (a.status === b.status ? 0 : a.status === "open" ? -1 : 1));
    return json(items as unknown as Json);
  }
  if (path === "/api/appeals/preview" && body) {
    const found = findCriterion(Number(body.evaluation_criterion_id));
    if (!found) return error(404, "not_found", "criterion not found");
    const total = previewTotal(found.ev, found.crit.id, (body.new_answer as string | number | null) ?? null);
    return json({ final_total_score: total, sections: found.ev.sections, critical_error: found.ev.critical_error });
  }
  if (path === "/api/appeals" && method === "POST" && body) {
    const found = findCriterion(Number(body.evaluation_criterion_id));
    if (!found) return error(404, "not_found", "criterion not found");
    if (found.crit.appeal && found.crit.appeal.status === "open") return error(409, "appeal_open", "an appeal is already open for this criterion");
    const appeal: Appeal = {
      id: state.nextAppealId++,
      evaluation_criterion_id: found.crit.id,
      comment: String(body.comment ?? ""),
      author_label: String(body.author_label ?? "QA analyst"),
      status: "open",
      created_at: new Date().toISOString(),
      decision: null,
    };
    found.crit.appeal = appeal;
    found.crit.appeals = [...(found.crit.appeals ?? []), appeal];
    found.crit.review_state = "under_review";
    found.ev.review_state = "under_review";
    const s = state.conversations.find((c) => c.id === found.ev.conversation_id);
    if (s) s.review_state = "under_review";
    const version = state.versions.get(found.ev.checklist_version_id);
    const def = version?.criteria.find((c) => c.code === found.crit.code);
    state.appeals.unshift({
      ...appeal,
      conversation_id: found.ev.conversation_id,
      evaluation_id: found.ev.id,
      code: found.crit.code,
      title: found.crit.title,
      answer_type: found.crit.answer_type ?? def?.answer_type ?? "binary",
      options: found.crit.options ?? def?.options ?? [],
      na_allowed: def?.na_allowed ?? false,
      applicable: found.crit.applicable,
      answer: found.crit.answer,
      na_reason: found.crit.na_reason,
      score: found.crit.score,
      max_points: found.crit.max_points,
      weight: found.crit.weight,
      explanation: found.crit.explanation,
      evidence: found.crit.evidence,
      total_score: found.ev.total_score,
      final_total_score: found.ev.final_total_score,
      review_state: "under_review",
      final_answer: null,
      language: s?.language,
      channel: s?.channel,
    });
    return json(appeal as unknown as Json);
  }
  if (r[0] === "appeals" && r[1] && r[2] === "decision" && body) {
    const ap = state.appeals.find((a) => a.id === Number(r[1]));
    if (!ap) return error(404, "not_found", "appeal not found");
    if (ap.status === "decided") return error(409, "already_decided", "appeal already decided");
    const found = findCriterion(ap.evaluation_criterion_id);
    const action = body.action as "confirm" | "change";
    const newAnswer = (body.new_answer as string | number | null) ?? null;
    let newScore: number | null = null;
    if (found) {
      const crit = found.crit;
      if (action === "change") {
        const opt = crit.options?.find((o) => String(o.value) === String(newAnswer));
        newScore = newAnswer === null ? 0 : opt?.points ?? 0;
        crit.review_state = "changed";
        crit.final_answer = newAnswer;
        crit.final_score = newScore;
        if (newAnswer === null) {
          crit.applicable = false;
          crit.na_reason = String(body.reason ?? "");
        }
      } else {
        crit.review_state = "confirmed";
        crit.final_answer = crit.answer;
        crit.final_score = crit.score;
      }
      recalc(found.ev);
      found.ev.review_state = "reviewed";
      const s = state.conversations.find((c) => c.id === found.ev.conversation_id);
      if (s) s.review_state = "reviewed";
    }
    const decision = {
      id: ap.id,
      action,
      new_answer: action === "change" ? newAnswer : null,
      new_score: newScore,
      reason: String(body.reason ?? ""),
      reviewer_label: String(body.reviewer_label ?? "Reviewer"),
      created_at: new Date().toISOString(),
    };
    ap.status = "decided";
    ap.decision = decision;
    ap.review_state = action === "change" ? "changed" : "confirmed";
    ap.final_answer = action === "change" ? newAnswer : ap.answer;
    ap.final_total_score = found?.ev.final_total_score ?? ap.final_total_score;
    if (found) {
      found.crit.appeal = { ...found.crit.appeal!, status: "decided", decision };
      found.crit.appeals = (found.crit.appeals ?? []).map((a) => (a.id === ap.id ? { ...a, status: "decided", decision } : a));
    }
    return json({ appeal: { ...ap }, evaluation: found?.ev ?? null } as unknown as Json);
  }

  return error(404, "not_found", `mock: no route for ${method} ${path}`);
}
