import * as React from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { api } from "@/api/endpoints";
import type { ConversationDetail } from "@/api/types";
import { notify, notifyError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export function UploadDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated?: (conv: ConversationDetail, evaluate: boolean) => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [tab, setTab] = React.useState("paste");
  const [file, setFile] = React.useState<File | null>(null);
  const [text, setText] = React.useState("");
  const [convId, setConvId] = React.useState("");
  const [channel, setChannel] = React.useState("agent chat");
  const [language, setLanguage] = React.useState("RU");
  const [busy, setBusy] = React.useState<"create" | "evaluate" | null>(null);

  const submit = async (evaluate: boolean) => {
    setBusy(evaluate ? "evaluate" : "create");
    try {
      let conv: ConversationDetail;
      if (tab === "file") {
        if (!file) return;
        conv = await api.uploadFile(file);
      } else {
        conv = await api.pasteTranscript({ text, channel, language, conversation_id: convId.trim() || null });
      }
      notify(t("conversations.uploadDialog.created", { id: conv.id }));
      void qc.invalidateQueries({ queryKey: ["conversations"] });
      onOpenChange(false);
      setText("");
      setFile(null);
      setConvId("");
      onCreated?.(conv, evaluate);
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(null);
    }
  };

  const canSubmit = tab === "file" ? !!file : text.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent width="640px">
        <DialogHeader>
          <DialogTitle>{t("conversations.uploadDialog.title")}</DialogTitle>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="paste">{t("conversations.uploadDialog.tabPaste")}</TabsTrigger>
              <TabsTrigger value="file">{t("conversations.uploadDialog.tabFile")}</TabsTrigger>
            </TabsList>
            <TabsContent value="paste" className="mt-3 flex flex-col gap-3">
              <div className="text-caption text-text-2">{t("conversations.uploadDialog.pasteHint")}</div>
              <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={t("conversations.uploadDialog.pastePlaceholder")} className="min-h-[180px] font-mono text-[12px]" />
              <div className="grid grid-cols-3 gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label>{t("conversations.uploadDialog.conversationId")}</Label>
                  <Input value={convId} onChange={(e) => setConvId(e.target.value)} placeholder="manual-001" className="font-mono" />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label>{t("common.channel")}</Label>
                  <Select value={channel} onValueChange={setChannel}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="agent chat">{t("channel.agent chat")}</SelectItem>
                      <SelectItem value="voice call">{t("channel.voice call")}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label>{t("common.language")}</Label>
                  <Select value={language} onValueChange={setLanguage}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="RU">RU</SelectItem>
                      <SelectItem value="KZ">KZ</SelectItem>
                      <SelectItem value="EN">EN</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </TabsContent>
            <TabsContent value="file" className="mt-3 flex flex-col gap-3">
              <div className="text-caption text-text-2">{t("conversations.uploadDialog.fileHint")}</div>
              <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border border-dashed border-line px-4 py-8 text-caption text-text-2 hover:bg-surface-2/60">
                <Upload className="size-5" />
                <span>{file ? file.name : t("conversations.uploadDialog.chooseFile")}</span>
                <input type="file" accept=".json,.txt,application/json,text/plain" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </label>
            </TabsContent>
          </Tabs>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button variant="secondary" onClick={() => submit(false)} loading={busy === "create"} disabled={!canSubmit || busy === "evaluate"}>
            {t("conversations.uploadDialog.submit")}
          </Button>
          <Button variant="primary" onClick={() => submit(true)} loading={busy === "evaluate"} disabled={!canSubmit || busy === "create"}>
            {t("conversations.uploadDialog.evaluateNow")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
