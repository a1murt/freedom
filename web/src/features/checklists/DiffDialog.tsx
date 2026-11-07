import * as React from "react";
import { useTranslation } from "react-i18next";
import { useDiff } from "@/api/queries";
import type { Checklist } from "@/api/types";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { ErrorState, LoadingBlock } from "@/components/common";

export function DiffDialog({ checklist, open, onOpenChange, initialA, initialB }: { checklist: Checklist; open: boolean; onOpenChange: (o: boolean) => void; initialA?: number; initialB?: number }) {
  const { t } = useTranslation();
  const sorted = React.useMemo(() => [...checklist.versions].sort((x, y) => x.version_no - y.version_no), [checklist.versions]);
  const [a, setA] = React.useState<string>(String(initialA ?? sorted[Math.max(0, sorted.length - 2)]?.id ?? ""));
  const [b, setB] = React.useState<string>(String(initialB ?? sorted[sorted.length - 1]?.id ?? ""));
  React.useEffect(() => {
    if (open) {
      setA(String(initialA ?? sorted[Math.max(0, sorted.length - 2)]?.id ?? ""));
      setB(String(initialB ?? sorted[sorted.length - 1]?.id ?? ""));
    }
  }, [open, initialA, initialB, sorted]);
  const diff = useDiff(checklist.id, a ? Number(a) : null, b ? Number(b) : null);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent width="680px">
        <DialogHeader>
          <DialogTitle>{t("checklists.diff.title")}</DialogTitle>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>{t("checklists.diff.from")}</Label>
              <Select value={a} onValueChange={setA}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {sorted.map((v) => (
                    <SelectItem key={v.id} value={String(v.id)}>
                      v{v.version_no} · {v.change_note || "—"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>{t("checklists.diff.to")}</Label>
              <Select value={b} onValueChange={setB}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {sorted.map((v) => (
                    <SelectItem key={v.id} value={String(v.id)}>
                      v{v.version_no} · {v.change_note || "—"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          {a === b ? (
            <div className="text-caption text-text-2">{t("checklists.diff.same")}</div>
          ) : diff.isLoading ? (
            <LoadingBlock rows={3} />
          ) : diff.error ? (
            <ErrorState error={diff.error} />
          ) : diff.data ? (
            <DiffBody diff={diff.data} />
          ) : null}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

export function DiffBody({ diff }: { diff: NonNullable<ReturnType<typeof useDiff>["data"]> }) {
  const { t } = useTranslation();
  const empty = diff.added.length === 0 && diff.removed.length === 0 && diff.changed.length === 0 && !diff.scoring_rules_changed;
  if (empty) return <div className="text-caption text-text-2">{t("checklists.diff.empty")}</div>;
  return (
    <div className="flex flex-col gap-1.5">
      {diff.scoring_rules_changed ? (
        <Row tone="warning" code="scoring_rules" label={t("checklists.diff.scoringChanged")} />
      ) : null}
      {diff.added.map((c) => (
        <Row key={`a${c}`} tone="success" code={c} label={t("checklists.diff.added")} rejudge />
      ))}
      {diff.removed.map((c) => (
        <Row key={`r${c}`} tone="critical" code={c} label={t("checklists.diff.removed")} />
      ))}
      {diff.changed.map((c) => (
        <Row key={`c${c.code}`} tone="info" code={c.code} label={`${t("checklists.diff.changed")}: ${c.fields.join(", ")}`} rejudge={c.requires_rejudge} />
      ))}
    </div>
  );
}

function Row({ tone, code, label, rejudge }: { tone: "success" | "critical" | "info" | "warning"; code: string; label: string; rejudge?: boolean }) {
  const { t } = useTranslation();
  const bar = tone === "success" ? "bg-success" : tone === "critical" ? "bg-critical" : tone === "warning" ? "bg-warning" : "bg-info";
  return (
    <div className="flex items-center gap-3 rounded-sm border border-line px-3 py-2 text-caption">
      <span className={`h-5 w-1 rounded-full ${bar}`} />
      <span className="w-16 font-mono font-semibold">{code}</span>
      <span className="flex-1 text-text-2">{label}</span>
      {rejudge !== undefined ? <Badge variant={rejudge ? "warning" : "outline"}>{rejudge ? t("checklists.diff.rejudge") : t("checklists.diff.noRejudge")}</Badge> : null}
    </div>
  );
}
