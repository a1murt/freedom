import * as React from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/api/endpoints";
import type { EvaluationCriterion } from "@/api/types";
import { notify, notifyError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { AnswerLabel } from "@/components/common";

export function AppealDialog({ criterion, open, onOpenChange, onCreated }: { criterion: EvaluationCriterion | null; open: boolean; onOpenChange: (o: boolean) => void; onCreated: () => void }) {
  const { t } = useTranslation();
  const [comment, setComment] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (open) setComment("");
  }, [open]);

  const submit = async () => {
    if (!criterion) return;
    setBusy(true);
    try {
      await api.createAppeal({ evaluation_criterion_id: criterion.id, comment: comment.trim(), author_label: t("role.analyst") });
      notify(t("card.appealDialog.created"));
      onCreated();
      onOpenChange(false);
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("card.appealDialog.title", { code: criterion?.code ?? "" })}</DialogTitle>
          {criterion ? (
            <DialogDescription>
              {criterion.title} · <AnswerLabel answer={criterion.answer} applicable={criterion.applicable} answerType={criterion.answer_type} /> · {criterion.score ?? "—"}/{criterion.max_points}
            </DialogDescription>
          ) : null}
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3">
          {criterion ? <div className="rounded-sm border border-line bg-surface-2/60 px-3 py-2 text-caption text-text-2">{criterion.explanation}</div> : null}
          <div className="flex flex-col gap-1.5">
            <Label>{t("card.appealDialog.comment")}</Label>
            <Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder={t("card.appealDialog.placeholder")} className="min-h-[110px]" autoFocus />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={submit} loading={busy} disabled={!comment.trim()}>
            {t("card.appealDialog.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
