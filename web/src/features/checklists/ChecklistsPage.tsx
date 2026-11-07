import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileUp, GitCompare, Pencil, Plus } from "lucide-react";
import { api } from "@/api/endpoints";
import { useChecklists } from "@/api/queries";
import type { ChecklistBody, ChecklistVersion } from "@/api/types";
import { fmtDate } from "@/lib/format";
import { dateLocale } from "@/i18n";
import { notify, notifyError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingBlock, PageHeader } from "@/components/common";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DiffDialog } from "./DiffDialog";
import { toChecklistBody } from "./model";

export default function ChecklistsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const lists = useChecklists();
  const [diffFor, setDiffFor] = React.useState<number | null>(null);
  const fileInput = React.useRef<HTMLInputElement>(null);

  const activate = async (checklistId: number, versionId: number, no: number) => {
    try {
      await api.activate(checklistId, versionId);
      notify(t("checklists.editor.activated", { no }));
      void qc.invalidateQueries({ queryKey: ["checklists"] });
    } catch (e) {
      notifyError(e);
    }
  };

  const importFile = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text()) as Partial<ChecklistVersion> & { name?: string; description?: string };
      if (!Array.isArray(parsed.criteria)) throw new Error(t("checklists.editor.importBad"));
      const body: ChecklistBody = { ...toChecklistBody(parsed as ChecklistVersion), name: parsed.name ?? file.name.replace(/\.json$/i, ""), description: parsed.description ?? "", change_note: `import ${file.name}`, compile_specs: true };
      const v = await api.createChecklist(body);
      notify(t("checklists.editor.saved", { no: v.version_no }));
      void qc.invalidateQueries({ queryKey: ["checklists"] });
      navigate(`/checklists/${v.checklist_id}/versions/${v.id}`);
    } catch (e) {
      notifyError(e);
    }
  };

  return (
    <div className="flex flex-col gap-4 p-6">
      <PageHeader
        title={t("checklists.title")}
        subtitle={t("checklists.subtitle")}
        actions={
          <>
            <input ref={fileInput} type="file" accept="application/json,.json" className="hidden" onChange={(e) => e.target.files?.[0] && void importFile(e.target.files[0])} />
            <Button variant="secondary" onClick={() => fileInput.current?.click()}>
              <FileUp /> {t("checklists.newChecklist")} ({t("common.importJson")})
            </Button>
          </>
        }
      />
      {lists.isLoading ? (
        <LoadingBlock rows={5} />
      ) : lists.error ? (
        <ErrorState error={lists.error} onRetry={() => lists.refetch()} />
      ) : (lists.data ?? []).length === 0 ? (
        <EmptyState title={t("checklists.empty")} mascot="typing" />
      ) : (
        (lists.data ?? []).map((cl) => (
          <div key={cl.id} className="card overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
              <div>
                <div className="text-h3 font-display">{cl.name}</div>
                {cl.description ? <div className="text-caption text-text-2">{cl.description}</div> : null}
              </div>
              <div className="flex items-center gap-2">
                {cl.active_version ? (
                  <Badge variant="accent">
                    <CheckCircle2 className="size-3" /> v{cl.active_version.version_no} · {cl.active_version.criteria.length} {t("checklists.editor.criteria").toLowerCase()}
                  </Badge>
                ) : null}
                <Button variant="secondary" size="sm" onClick={() => setDiffFor(cl.id)} disabled={cl.versions.length < 2}>
                  <GitCompare /> {t("checklists.editor.diff")}
                </Button>
                {cl.active_version ? (
                  <Button variant="primary" size="sm" onClick={() => navigate(`/checklists/${cl.id}/versions/${cl.active_version!.id}`)}>
                    <Pencil /> {t("checklists.openEditor")}
                  </Button>
                ) : null}
              </div>
            </div>
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("common.version")}</TableHead>
                  <TableHead>{t("checklists.editor.changeNote")}</TableHead>
                  <TableHead>{t("common.date")}</TableHead>
                  <TableHead>{t("common.status")}</TableHead>
                  <TableHead className="text-right" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...cl.versions]
                  .sort((a, b) => b.version_no - a.version_no)
                  .map((v) => (
                    <TableRow key={v.id}>
                      <TableCell>
                        <Link to={`/checklists/${cl.id}/versions/${v.id}`} className="font-mono font-medium hover:text-accent">
                          v{v.version_no}
                        </Link>
                      </TableCell>
                      <TableCell className="max-w-md truncate text-text-2">{v.change_note || "—"}</TableCell>
                      <TableCell className="text-text-2">{fmtDate(v.created_at, dateLocale())}</TableCell>
                      <TableCell>{v.is_active ? <Badge variant="accent">{t("common.active")}</Badge> : <span className="text-text-2">—</span>}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          {!v.is_active ? (
                            <Button size="sm" variant="ghost" onClick={() => activate(cl.id, v.id, v.version_no)}>
                              {t("common.activate")}
                            </Button>
                          ) : null}
                          <Button size="sm" variant="ghost" asChild>
                            <Link to={`/checklists/${cl.id}/versions/${v.id}`}>{t("common.open")}</Link>
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
            <DiffDialog checklist={cl} open={diffFor === cl.id} onOpenChange={(o) => !o && setDiffFor(null)} />
          </div>
        ))
      )}
      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={() => fileInput.current?.click()}>
          <Plus /> {t("checklists.newChecklist")}
        </Button>
      </div>
    </div>
  );
}
