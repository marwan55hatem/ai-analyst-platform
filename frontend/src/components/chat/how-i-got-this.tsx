"use client";

import { CheckCircle2, ChevronRight, CircleDashed, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";

import { CopyButton } from "@/components/common/copy-button";
import { EngineText } from "@/components/insights/badges";
import { EvidenceBlock } from "@/components/insights/insight-drawer";
import { Button } from "@/components/ui/button";
import type { ToolStep } from "@/lib/chat/events";
import { useFormat } from "@/lib/format";
import { cn } from "@/lib/utils";

const TOOL_NAMES = [
  "get_dataset_overview",
  "run_sql",
  "run_python",
  "get_insights",
  "get_forecast",
  "get_anomalies",
  "get_segments",
  "get_churn_risk",
  "explain_drivers",
  "what_if",
  "make_chart",
] as const;
const ROWS_SHOWN = 50;

interface LinkedInsight {
  insight_id: string;
  title?: string;
  type?: string;
}

/** The plain name of a tool; one this version does not know keeps its own. */
export function useToolName() {
  const t = useTranslations("chat.tools");
  return (name: string) => {
    const known = TOOL_NAMES.find((tool) => tool === name);
    return known ? t(known) : name;
  };
}

function CodeBlock({ label, code }: { label: string; code: string }) {
  return (
    <div className="rounded-md border bg-muted">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        <CopyButton text={code} />
      </div>
      <pre className="max-h-48 overflow-auto p-3 text-xs leading-relaxed" dir="ltr" tabIndex={0}>
        <code>{code}</code>
      </pre>
    </div>
  );
}

function ToolResultRows({ evidence }: { evidence: Record<string, unknown> }) {
  const t = useTranslations("chat.how");
  const fmt = useFormat();
  const rows = Array.isArray(evidence.rows) ? (evidence.rows as unknown[][]) : [];
  const total = typeof evidence.row_count === "number" ? evidence.row_count : rows.length;
  return (
    <>
      <EvidenceBlock
        query={{
          purpose: t("query"),
          sql: String(evidence.sql),
          columns: Array.isArray(evidence.columns) ? (evidence.columns as string[]) : [],
          rows: rows.slice(0, ROWS_SHOWN),
          row_count: total,
        }}
      />
      {evidence.truncated ? (
        <p className="text-xs text-muted-foreground">{t("capped", { cap: fmt.int(total) })}</p>
      ) : null}
    </>
  );
}

function ToolTrace({ step, onOpenInsight }: { step: ToolStep; onOpenInsight: (id: string) => void }) {
  const t = useTranslations("chat.how");
  const name = useToolName();
  const { evidence } = step;
  const insights = Array.isArray(evidence.insights) ? (evidence.insights as LinkedInsight[]) : [];
  const Icon = step.ok === null ? CircleDashed : step.ok ? CheckCircle2 : XCircle;
  return (
    <li className="space-y-2" data-testid="tool-step" data-tool={step.name}>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <span className="font-medium">{name(step.name)}</span>
        <span
          className={cn(
            "inline-flex items-center gap-1 text-xs",
            step.ok === false ? "text-bad" : step.ok ? "text-good" : "text-muted-foreground",
          )}
        >
          <Icon className="size-3.5" aria-hidden="true" />
          {step.ok === null ? t("unfinished") : step.ok ? t("done") : t("failed")}
        </span>
        {step.ok && step.summary ? (
          <EngineText className="text-xs text-muted-foreground">{step.summary}</EngineText>
        ) : null}
      </p>
      {step.ok === false && typeof evidence.message === "string" ? (
        <p className="text-sm">
          <EngineText>{evidence.message}</EngineText>
        </p>
      ) : null}
      {typeof evidence.sql === "string" ? <ToolResultRows evidence={evidence} /> : null}
      {typeof evidence.code === "string" ? <CodeBlock label={t("code")} code={evidence.code} /> : null}
      {typeof evidence.stdout === "string" && evidence.stdout ? (
        <CodeBlock label={t("output")} code={evidence.stdout} />
      ) : null}
      {insights.length ? (
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground">{t("findings")}</p>
          <ul className="space-y-1">
            {insights.map((insight) => (
              <li key={insight.insight_id}>
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  className="h-auto whitespace-normal p-0 text-start"
                  onClick={() => onOpenInsight(insight.insight_id)}
                >
                  <EngineText>{insight.title ?? insight.type ?? insight.insight_id}</EngineText>
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </li>
  );
}

/** The tools behind an answer. Results can be 1,000 rows, so nothing mounts until it is opened. */
export function HowIGotThis({
  tools,
  onOpenInsight,
}: {
  tools: ToolStep[];
  onOpenInsight: (id: string) => void;
}) {
  const t = useTranslations("chat.how");
  const [open, setOpen] = useState(false);
  const panel = useId();
  if (!tools.length) return <p className="text-sm text-muted-foreground">{t("none")}</p>;
  return (
    <div className="rounded-md border" data-testid="how-i-got-this">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panel}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-start text-sm font-medium outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronRight
          className={cn("size-4 shrink-0 rtl:-scale-x-100", open && "rotate-90 rtl:-rotate-90")}
          aria-hidden="true"
        />
        {t("title")}
        <span className="font-normal text-muted-foreground">{t("steps", { count: tools.length })}</span>
      </button>
      {open ? (
        <ol id={panel} className="space-y-5 border-t p-3">
          {tools.map((step) => (
            <ToolTrace key={step.id} step={step} onOpenInsight={onOpenInsight} />
          ))}
        </ol>
      ) : null}
    </div>
  );
}
