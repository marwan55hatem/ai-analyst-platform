"use client";

import { Lock } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useErrorMessage } from "@/components/common/error-state";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ROLES, type ColumnProfile, type Role } from "@/lib/api/types";
import { useSetRole } from "@/lib/queries";

/** Columns with their detected type and an editable role. */
export function ColumnsTable({
  datasetId,
  version,
  columns,
  onRoleChanged,
}: {
  datasetId: string;
  version: number;
  columns: ColumnProfile[];
  onRoleChanged: () => void;
}) {
  const t = useTranslations("columns");
  const errorMessage = useErrorMessage();
  const setRole = useSetRole(datasetId, version);

  const typeLabel = (type: string) => (t.has(`types.${type}`) ? t(`types.${type}`) : type);
  const roleLabel = (role: string) => (t.has(`roles.${role}`) ? t(`roles.${role}`) : role);
  const sourceLabel = (source: string) => (t.has(`source.${source}`) ? t(`source.${source}`) : source);

  return (
    <div className="space-y-3">
      <p className="max-w-prose text-sm text-muted-foreground">{t("intro")}</p>
      <div className="overflow-x-auto rounded-lg border bg-card">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/60 hover:bg-muted/60">
              <TableHead className="ps-4">{t("colName")}</TableHead>
              <TableHead>{t("colType")}</TableHead>
              <TableHead>{t("colRole")}</TableHead>
              <TableHead className="pe-4">{t("colSource")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {columns.map((c) => {
              const label = c.original_name || c.name;
              return (
                <TableRow key={c.name} data-column={c.name}>
                  <TableCell className="max-w-[16rem] ps-4">
                    <div className="flex items-center gap-2">
                      <bdi className="truncate font-medium">{label}</bdi>
                      {c.pii_flag ? (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              className="inline-flex items-center gap-1 rounded-full bg-warn-soft px-2 py-0.5 text-xs font-medium whitespace-nowrap text-warn"
                            >
                              <Lock className="size-3" aria-hidden="true" />
                              {t("personal")}
                            </button>
                          </TooltipTrigger>
                          <TooltipContent className="max-w-xs">{t("personalHelp")}</TooltipContent>
                        </Tooltip>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{typeLabel(c.inferred_type)}</TableCell>
                  <TableCell>
                    <Select
                      value={c.semantic_role}
                      onValueChange={(role) => {
                        if (role === c.semantic_role) return;
                        setRole.mutate(
                          { column: c.name, role: role as Role },
                          {
                            onSuccess: () => {
                              toast.success(t("saved", { column: label }));
                              onRoleChanged();
                            },
                            onError: (error) => toast.error(errorMessage(error)),
                          },
                        );
                      }}
                    >
                      <SelectTrigger
                        className="h-8 w-44"
                        aria-label={t("roleFor", { column: label })}
                        aria-busy={setRole.isPending && setRole.variables?.column === c.name}
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ROLES.map((r) => (
                          <SelectItem key={r} value={r}>
                            {roleLabel(r)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell className="pe-4 text-muted-foreground">{sourceLabel(c.role_source)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
