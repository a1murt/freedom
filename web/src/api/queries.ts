import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { api, type ConversationFilters } from "./endpoints";
import type { Evaluation, Settings } from "./types";
import { ApiError } from "./client";

export const keys = {
  conversations: (f: ConversationFilters) => ["conversations", f] as const,
  conversation: (id: string) => ["conversation", id] as const,
  evaluation: (id: string) => ["evaluation", id] as const,
  evaluations: (id: string) => ["evaluations", id] as const,
  trace: (evaluationId: number) => ["trace", evaluationId] as const,
  audioFeatures: (id: string) => ["audio-features", id] as const,
  checklists: ["checklists"] as const,
  version: (c: number, v: number) => ["version", c, v] as const,
  diff: (c: number, a: number, b: number) => ["diff", c, a, b] as const,
  appeals: (status?: string) => ["appeals", status ?? "all"] as const,
  analytics: (v?: number | null) => ["analytics", v ?? null] as const,
  settings: ["settings"] as const,
  health: ["health"] as const,
  job: (id: string) => ["job", id] as const,
};

export function useConversations(f: ConversationFilters) {
  return useQuery({ queryKey: keys.conversations(f), queryFn: () => api.listConversations(f), placeholderData: (p) => p });
}

export function useConversation(id: string | null) {
  return useQuery({ queryKey: keys.conversation(id ?? ""), queryFn: () => api.getConversation(id!), enabled: !!id });
}

/** Latest evaluation; a 404 "no evaluation yet" resolves to null instead of an error. */
export function useLatestEvaluation(id: string | null, opts?: Partial<UseQueryOptions<Evaluation | null>>) {
  return useQuery<Evaluation | null>({
    queryKey: keys.evaluation(id ?? ""),
    queryFn: async () => {
      try {
        return await api.latestEvaluation(id!);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    enabled: !!id,
    ...opts,
  });
}

export function useEvaluationHistory(id: string | null) {
  return useQuery({ queryKey: keys.evaluations(id ?? ""), queryFn: () => api.evaluationHistory(id!), enabled: !!id });
}

export function useTrace(evaluationId: number | null) {
  return useQuery({ queryKey: keys.trace(evaluationId ?? 0), queryFn: () => api.trace(evaluationId!), enabled: !!evaluationId });
}

export function useAudioFeatures(id: string | null, enabled: boolean) {
  return useQuery({
    queryKey: keys.audioFeatures(id ?? ""),
    queryFn: () => api.audioFeatures(id!),
    enabled: !!id && enabled,
    retry: false,
  });
}

export function useChecklists() {
  return useQuery({ queryKey: keys.checklists, queryFn: api.checklists });
}

export function useVersion(checklistId: number | null, versionId: number | null) {
  return useQuery({
    queryKey: keys.version(checklistId ?? 0, versionId ?? 0),
    queryFn: () => api.version(checklistId!, versionId!),
    enabled: !!checklistId && !!versionId,
  });
}

export function useDiff(checklistId: number | null, a: number | null, b: number | null) {
  return useQuery({
    queryKey: keys.diff(checklistId ?? 0, a ?? 0, b ?? 0),
    queryFn: () => api.diff(checklistId!, a!, b!),
    enabled: !!checklistId && !!a && !!b && a !== b,
  });
}

export function useAppeals(status?: string) {
  return useQuery({ queryKey: keys.appeals(status), queryFn: () => api.appeals(status) });
}

export function useAnalytics(versionId?: number | null) {
  return useQuery({ queryKey: keys.analytics(versionId), queryFn: () => api.analytics(versionId) });
}

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: api.settings, staleTime: 60_000 });
}

export function useHealth() {
  return useQuery({ queryKey: keys.health, queryFn: api.health, staleTime: 30_000, retry: false });
}

export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<Settings>) => api.saveSettings(body),
    onSuccess: (data) => qc.setQueryData(keys.settings, data),
  });
}

export function useInvalidateConversation() {
  const qc = useQueryClient();
  return (id: string) => {
    void qc.invalidateQueries({ queryKey: keys.conversation(id) });
    void qc.invalidateQueries({ queryKey: keys.evaluation(id) });
    void qc.invalidateQueries({ queryKey: keys.evaluations(id) });
    void qc.invalidateQueries({ queryKey: ["conversations"] });
    void qc.invalidateQueries({ queryKey: ["appeals"] });
    void qc.invalidateQueries({ queryKey: ["analytics"] });
  };
}
