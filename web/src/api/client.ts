import { apiUrl, MOCK } from "@/lib/config";
import type { ApiErrorShape } from "./types";

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

let mockFetch: typeof fetch | null = null;

export async function getFetch(): Promise<typeof fetch> {
  if (!MOCK) return fetch.bind(window);
  if (!mockFetch) {
    const mod = await import("@/mock/server");
    mockFetch = mod.mockFetch;
  }
  return mockFetch;
}

async function parseError(res: Response): Promise<ApiError> {
  let code = `http_${res.status}`;
  let message = res.statusText || `HTTP ${res.status}`;
  try {
    const body = (await res.json()) as Partial<ApiErrorShape> & { detail?: unknown };
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
    } else if (body?.detail) {
      const d = body.detail as { code?: string; message?: string } | string;
      if (typeof d === "string") message = d;
      else {
        code = d.code ?? code;
        message = d.message ?? message;
      }
    }
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, code, message);
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  query?: Record<string, string | number | boolean | null | undefined>;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const f = await getFetch();
  let url = apiUrl(path);
  if (opts.query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.query)) {
      if (v === undefined || v === null || v === "") continue;
      qs.set(k, String(v));
    }
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  const init: RequestInit = { method: opts.method ?? (opts.body || opts.form ? "POST" : "GET"), signal: opts.signal };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) {
    init.headers = { "content-type": "application/json" };
    init.body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await f(url, init);
  } catch (e) {
    throw new ApiError(0, "network", (e as Error)?.message || "Network error");
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export const get = <T>(path: string, query?: RequestOptions["query"], signal?: AbortSignal) =>
  request<T>(path, { method: "GET", query, signal });
export const post = <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body: body ?? {} });
export const put = <T>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body: body ?? {} });
export const postForm = <T>(path: string, form: FormData) => request<T>(path, { method: "POST", form });
