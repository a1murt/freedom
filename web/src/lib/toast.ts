import { toast } from "sonner";
import i18n from "@/i18n";
import { ApiError } from "@/api/client";
import { apiBase } from "./config";

export function notify(message: string, description?: string) {
  toast.success(message, { description });
}

export function notifyInfo(message: string, description?: string) {
  toast(message, { description });
}

export function notifyError(e: unknown, fallback?: string) {
  const t = i18n.t.bind(i18n);
  if (e instanceof ApiError) {
    if (e.status === 0) {
      toast.error(t("errors.network", { base: apiBase() || window.location.origin }));
      return;
    }
    toast.error(fallback ?? t("common.error"), { description: t("errors.apiError", { code: e.code, message: e.message }) });
    return;
  }
  const msg = e instanceof Error ? e.message : String(e ?? "");
  toast.error(fallback ?? t("errors.generic"), { description: msg || undefined });
}

export function errorMessage(e: unknown): string {
  const t = i18n.t.bind(i18n);
  if (e instanceof ApiError) {
    if (e.status === 0) return t("errors.network", { base: apiBase() || window.location.origin });
    return t("errors.apiError", { code: e.code, message: e.message });
  }
  return e instanceof Error ? e.message : t("errors.generic");
}
