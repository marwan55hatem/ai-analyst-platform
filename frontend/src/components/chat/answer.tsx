"use client";

import { Loader2, RotateCcw } from "lucide-react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";

import { CopyButton } from "@/components/common/copy-button";
import { Button } from "@/components/ui/button";
import type { ChatTurn } from "@/lib/chat/events";

import { ChatError } from "./chat-error";
import { HowIGotThis, useToolName } from "./how-i-got-this";
import { StatusNotice } from "./status-notice";
import { TrustSignals } from "./trust-signals";

// Markdown and Recharts are the heaviest parts of the page, and an empty conversation needs neither.
const Markdown = dynamic(() => import("./markdown").then((m) => m.Markdown), {
  ssr: false,
  loading: () => null,
});
const ChatChart = dynamic(() => import("./chat-chart").then((m) => m.ChatChart), {
  ssr: false,
  loading: () => null,
});

function Progress({ turn }: { turn: ChatTurn }) {
  const t = useTranslations("chat");
  const name = useToolName();
  const tool = turn.tools.at(-1);
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground" data-testid="answer-progress">
      <Loader2 className="size-4 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      {tool ? t("running", { tool: name(tool.name) }) : t("thinking")}
    </p>
  );
}

/** One answer: text first, then its charts, what to watch out for, and how it was reached. */
export function Answer({
  turn,
  canRetry,
  onRetry,
  onOpenInsight,
}: {
  turn: ChatTurn;
  canRetry: boolean;
  onRetry: () => void;
  onOpenInsight: (id: string) => void;
}) {
  const t = useTranslations("chat");
  const streaming = turn.status === "streaming";
  const partial = turn.status === "stopped" || turn.status === "failed";
  return (
    <article aria-label={t("analyst")} className="min-w-0 space-y-3" data-testid="answer" data-status={turn.status}>
      {turn.answer ? (
        <div data-testid="answer-text">
          <Markdown text={turn.answer} />
        </div>
      ) : null}
      {turn.waitingUntil ? <StatusNotice key={turn.waitingUntil} until={turn.waitingUntil} /> : null}
      {streaming && !turn.waitingUntil ? <Progress turn={turn} /> : null}
      {partial && turn.answer ? (
        <p className="text-sm text-muted-foreground" data-testid="answer-stopped">
          {turn.status === "stopped" ? t("stopped") : t("interrupted")}
        </p>
      ) : null}
      {turn.status === "unanswered" ? (
        <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground" data-testid="answer-missing">
          <p>{t("noAnswer")}</p>
          {canRetry ? (
            <Button type="button" variant="outline" size="sm" onClick={onRetry}>
              <RotateCcw aria-hidden="true" />
              {t("retry")}
            </Button>
          ) : null}
        </div>
      ) : null}
      {turn.error ? <ChatError error={turn.error} onRetry={onRetry} /> : null}
      {turn.charts.map((spec, index) => (
        <ChatChart key={`${spec.title}-${index}`} spec={spec} />
      ))}
      <TrustSignals turn={turn} />
      {turn.answer && !streaming ? (
        <div className="-ms-2">
          <CopyButton text={turn.answer} />
        </div>
      ) : null}
      {!streaming && turn.status !== "unanswered" ? <HowIGotThis tools={turn.tools} onOpenInsight={onOpenInsight} /> : null}
    </article>
  );
}
