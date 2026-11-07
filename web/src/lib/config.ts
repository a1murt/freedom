/**
 * Runtime configuration.
 *
 * API base resolution order (first non-null wins):
 *   1. `?api=` query parameter (before the hash, or inside the hash route query)
 *   2. `window.CQA_CONFIG.apiUrl` (set by a host page, e.g. a Dynamics 365 web resource)
 *   3. `import.meta.env.VITE_API_URL` (build time; empty string = same origin)
 */
declare global {
  interface Window {
    CQA_CONFIG?: { apiUrl?: string; embed?: boolean; conversation?: string };
  }
}

function allParams(): URLSearchParams {
  const p = new URLSearchParams(window.location.search);
  const hash = window.location.hash || "";
  const qi = hash.indexOf("?");
  if (qi >= 0) {
    const hp = new URLSearchParams(hash.slice(qi + 1));
    hp.forEach((v, k) => {
      if (!p.has(k)) p.set(k, v);
    });
  }
  return p;
}

export function param(name: string): string | null {
  return allParams().get(name);
}

export function apiBase(): string {
  const q = param("api");
  const cfg = window.CQA_CONFIG?.apiUrl;
  const env = (import.meta.env.VITE_API_URL as string | undefined) ?? "";
  const base = (q ?? cfg ?? env ?? "").trim();
  return base.replace(/\/+$/, "");
}

export const MOCK = String(import.meta.env.VITE_MOCK ?? "") === "1" || param("mock") === "1";

export function embedConfig(): { embed: boolean; conversation: string | null } {
  const embed = param("embed") === "1" || window.CQA_CONFIG?.embed === true;
  const conversation = param("conversation") ?? window.CQA_CONFIG?.conversation ?? null;
  return { embed, conversation };
}

/** Absolute URL for an API path, e.g. apiUrl('/api/audio/p4-ru-001'). */
export function apiUrl(path: string): string {
  return `${apiBase()}${path.startsWith("/") ? path : `/${path}`}`;
}
