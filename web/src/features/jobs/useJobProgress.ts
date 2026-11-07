import * as React from "react";
import { api } from "@/api/endpoints";
import type { Job, JobLog } from "@/api/types";
import { apiUrl, MOCK } from "@/lib/config";

export interface JobProgressState {
  status: string;
  done: number;
  total: number;
  failed: number;
  cost: number;
  live: boolean; // true when fed by SSE, false when polling
  log: JobLog[];
  lastConversation: string | null;
  job: Job | null;
}

const TERMINAL = new Set(["completed", "failed", "cancelled", "missing"]);

/**
 * Follows a job: EventSource on /api/jobs/{id}/events (events job.progress, evaluation.done,
 * job.status, job.done), falling back to polling GET /api/jobs/{id} every second.
 */
export function useJobProgress(jobId: string | null, onDone?: (status: string, job: Job | null) => void) {
  const [state, setState] = React.useState<JobProgressState>({ status: "queued", done: 0, total: 0, failed: 0, cost: 0, live: false, log: [], lastConversation: null, job: null });
  const doneRef = React.useRef(onDone);
  doneRef.current = onDone;

  React.useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let es: EventSource | null = null;
    let pollTimer: number | null = null;
    let finished = false;

    const finish = async (status: string) => {
      if (finished || cancelled) return;
      finished = true;
      es?.close();
      if (pollTimer) window.clearInterval(pollTimer);
      let job: Job | null = null;
      try {
        job = await api.job(jobId);
      } catch {
        /* ignore */
      }
      if (cancelled) return;
      setState((s) => ({
        ...s,
        status: job?.status ?? status,
        done: job?.done ?? s.done,
        total: job?.total ?? s.total,
        failed: job?.failed ?? s.failed,
        cost: job?.cost_usd ?? s.cost,
        log: job?.log ?? s.log,
        job,
      }));
      doneRef.current?.(job?.status ?? status, job);
    };

    const applyJob = (job: Job) => {
      setState((s) => ({ ...s, status: job.status, done: job.done, total: job.total, failed: job.failed, cost: job.cost_usd, log: job.log ?? s.log, job }));
      if (TERMINAL.has(job.status)) void finish(job.status);
    };

    const startPolling = () => {
      if (pollTimer || finished) return;
      setState((s) => ({ ...s, live: false }));
      const tick = async () => {
        try {
          const job = await api.job(jobId);
          if (!cancelled) applyJob(job);
        } catch {
          /* keep polling */
        }
      };
      void tick();
      pollTimer = window.setInterval(tick, 1000);
    };

    // Initial snapshot (also gives us the total and the log).
    void api.job(jobId).then((j) => !cancelled && applyJob(j)).catch(() => undefined);

    if (MOCK || typeof EventSource === "undefined") {
      startPolling();
    } else {
      let gotMessage = false;
      try {
        es = new EventSource(apiUrl(`/api/jobs/${encodeURIComponent(jobId)}/events`));
      } catch {
        es = null;
      }
      if (!es) startPolling();
      else {
        const parse = (e: MessageEvent) => {
          gotMessage = true;
          try {
            return JSON.parse(e.data) as Record<string, unknown>;
          } catch {
            return {};
          }
        };
        es.onopen = () => setState((s) => ({ ...s, live: true }));
        es.addEventListener("job.progress", (e) => {
          const d = parse(e as MessageEvent);
          setState((s) => ({ ...s, live: true, done: Number(d.done ?? s.done), total: Number(d.total ?? s.total), failed: Number(d.failed ?? s.failed), cost: Number(d.cost_usd ?? s.cost) }));
        });
        es.addEventListener("evaluation.done", (e) => {
          const d = parse(e as MessageEvent);
          setState((s) => ({
            ...s,
            lastConversation: String(d.conversation_id ?? s.lastConversation ?? ""),
            log: [...s.log.slice(-199), { ts: new Date().toISOString(), level: "info", message: `${String(d.conversation_id ?? "")}: total=${String(d.total_score ?? "")} critical=${String(d.critical_error ?? "")}` }],
          }));
        });
        es.addEventListener("evaluation.started", (e) => {
          const d = parse(e as MessageEvent);
          setState((s) => ({ ...s, status: "running", lastConversation: String(d.conversation_id ?? "") }));
        });
        es.addEventListener("job.status", (e) => {
          const d = parse(e as MessageEvent);
          setState((s) => ({ ...s, live: true, status: String(d.status ?? s.status), done: Number(d.done ?? s.done), total: Number(d.total ?? s.total), failed: Number(d.failed ?? s.failed), cost: Number(d.cost_usd ?? s.cost) }));
        });
        es.addEventListener("job.done", (e) => {
          const d = parse(e as MessageEvent);
          void finish(String(d.status ?? "completed"));
        });
        es.onerror = () => {
          // Server closed the stream (job finished) or SSE unavailable: fall back to polling.
          es?.close();
          es = null;
          if (!finished) {
            if (!gotMessage) startPolling();
            else void api.job(jobId).then((j) => (TERMINAL.has(j.status) ? finish(j.status) : startPolling())).catch(() => startPolling());
          }
        };
      }
    }

    return () => {
      cancelled = true;
      es?.close();
      if (pollTimer) window.clearInterval(pollTimer);
    };
  }, [jobId]);

  return state;
}
