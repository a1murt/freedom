import * as React from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/endpoints";
import { notify, notifyError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function ImportDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [groupId, setGroupId] = React.useState("DEV");
  const [busy, setBusy] = React.useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      const res = await api.importDataset(groupId.trim());
      const langs = Object.entries(res.by_language ?? {})
        .map(([k, v]) => `${k} ${v}`)
        .join(", ");
      const channels = Object.entries(res.by_channel ?? {})
        .map(([k, v]) => `${k} ${v}`)
        .join(", ");
      notify(t("conversations.importDialog.success", { count: res.imported }), t("conversations.importDialog.breakdown", { langs, channels }));
      void qc.invalidateQueries({ queryKey: ["conversations"] });
      void qc.invalidateQueries({ queryKey: ["analytics"] });
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
          <DialogTitle>{t("conversations.importDialog.title")}</DialogTitle>
          <DialogDescription>{t("conversations.importDialog.description")}</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-1.5">
          <Label htmlFor="group-id">{t("conversations.importDialog.groupId")}</Label>
          <Input id="group-id" value={groupId} onChange={(e) => setGroupId(e.target.value)} placeholder={t("conversations.importDialog.placeholder")} className="font-mono" autoFocus onKeyDown={(e) => e.key === "Enter" && groupId.trim() && void submit()} />
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={submit} loading={busy} disabled={!groupId.trim()}>
            {t("conversations.importDialog.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
