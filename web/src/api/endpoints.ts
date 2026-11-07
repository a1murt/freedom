import { get, post, postForm, put } from "./client";
import type {
  AnalyticsSummary,
  Appeal,
  AppealWithContext,
  AudioFeatures,
  Checklist,
  ChecklistBody,
  ChecklistDiff,
  ChecklistVersion,
  ConversationDetail,
  ConversationList,
  Criterion,
  DecisionBody,
  Evaluation,
  Health,
  ImportResult,
  Job,
  PreviewResult,
  RunBody,
  RunResult,
  Settings,
  TraceEntry,
} from "./types";

export interface ConversationFilters {
  channel?: string;
  language?: string;
  status?: string;
  min_total?: number | null;
  max_total?: number | null;
  critical?: boolean | null;
  q?: string;
  page?: number;
  page_size?: number;
}

export const api = {
  // conversations
  listConversations: (f: ConversationFilters = {}) =>
    get<ConversationList>("/api/conversations", { ...f, page_size: f.page_size ?? 200 }),
  getConversation: (id: string) => get<ConversationDetail>(`/api/conversations/${encodeURIComponent(id)}`),
  importDataset: (group_id: string) => post<ImportResult>("/api/datasets/import", { group_id }),
  uploadFile: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return postForm<ConversationDetail>("/api/conversations/upload", fd);
  },
  pasteTranscript: (body: { text: string; channel: string; language: string; conversation_id?: string | null }) =>
    post<ConversationDetail>("/api/conversations/paste", body),
  audioFeatures: (id: string) => get<AudioFeatures>(`/api/audio/${encodeURIComponent(id)}/features`),

  // evaluations
  latestEvaluation: (id: string) => get<Evaluation>(`/api/conversations/${encodeURIComponent(id)}/evaluation`),
  evaluationHistory: (id: string) => get<Evaluation[]>(`/api/conversations/${encodeURIComponent(id)}/evaluations`),
  getEvaluation: (evaluationId: number) => get<Evaluation>(`/api/evaluations/${evaluationId}`),
  trace: (evaluationId: number) => get<TraceEntry[]>(`/api/evaluations/${evaluationId}/trace`),
  run: (body: RunBody) => post<RunResult>("/api/evaluations/run", body),
  job: (id: string) => get<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  jobs: () => get<Job[]>("/api/jobs"),
  cancelJob: (id: string) => post<Job>(`/api/jobs/${encodeURIComponent(id)}/cancel`),

  // checklists
  checklists: () => get<Checklist[]>("/api/checklists"),
  version: (checklistId: number, versionId: number) =>
    get<ChecklistVersion>(`/api/checklists/${checklistId}/versions/${versionId}`),
  createChecklist: (body: ChecklistBody) => post<ChecklistVersion>("/api/checklists", body),
  createVersion: (checklistId: number, body: ChecklistBody) =>
    post<ChecklistVersion>(`/api/checklists/${checklistId}/versions`, body),
  activate: (checklistId: number, versionId: number) =>
    post<ChecklistVersion>(`/api/checklists/${checklistId}/versions/${versionId}/activate`),
  diff: (checklistId: number, a: number, b: number) =>
    get<ChecklistDiff>(`/api/checklists/${checklistId}/versions/${a}/diff/${b}`),
  regenerateSpec: (checklistId: number, versionId: number, code: string) =>
    post<Criterion>(`/api/checklists/${checklistId}/versions/${versionId}/judge-spec/${encodeURIComponent(code)}/regenerate`),

  // appeals
  appeals: (status?: string) => get<AppealWithContext[]>("/api/appeals", status ? { status } : undefined),
  createAppeal: (body: { evaluation_criterion_id: number; comment: string; author_label: string }) =>
    post<Appeal>("/api/appeals", body),
  decide: (appealId: number, body: DecisionBody) =>
    post<{ appeal: Appeal; evaluation: Evaluation }>(`/api/appeals/${appealId}/decision`, body),
  preview: (body: { evaluation_criterion_id: number; new_answer: string | number | null }) =>
    post<PreviewResult>("/api/appeals/preview", body),

  // analytics, settings, health
  analytics: (checklist_version_id?: number | null) =>
    get<AnalyticsSummary>("/api/analytics/summary", checklist_version_id ? { checklist_version_id } : undefined),
  settings: () => get<Settings>("/api/settings"),
  saveSettings: (body: Partial<Settings>) => put<Settings>("/api/settings", body),
  health: () => get<Health>("/api/health"),
};
