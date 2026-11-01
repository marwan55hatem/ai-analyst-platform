"use client";

import { AlertTriangle, CircleHelp, FlaskConical, ShieldCheck } from "lucide-react";
import { useTranslations } from "next-intl";

import { ConfidenceBadge, EngineText } from "@/components/insights/badges";
import type { Caveat, ChatTurn } from "@/lib/chat/events";

// Each of these reads as a warning of its own; untraced numbers have the panel below.
const KNOWN_CAVEATS = ["answer_truncated", "instruction_like_data", "sampled_data"] as const;

function Chip({ className, children, testId }: { className: string; children: React.ReactNode; testId: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${className}`}
      data-testid={testId}
    >
      {children}
    </span>
  );
}

function CaveatLine({ caveat }: { caveat: Caveat }) {
  const t = useTranslations("chat.caveats");
  const known = KNOWN_CAVEATS.find((kind) => kind === caveat.kind);
  return <li>{known ? t(known) : <EngineText>{caveat.message}</EngineText>}</li>;
}

/**
 * What the app can say about an answer: whether its numbers trace to the data, how sure it is and
 * what to watch out for. Nothing here comes from the model, and an answer that was never checked
 * is never shown as checked.
 */
export function TrustSignals({ turn }: { turn: ChatTurn }) {
  const t = useTranslations("chat.verifier");
  if (turn.status === "streaming" || turn.status === "unanswered") return null;
  const finished = turn.status === "done";
  const traced = finished && turn.untraced.length === 0 && (turn.numbersChecked ?? 0) > 0;
  const unchecked = !finished && Boolean(turn.answer);
  const caveats = turn.caveats.filter((c) => c.kind !== "untraced_numbers");
  return (
    <div className="space-y-2">
      {finished && turn.untraced.length ? (
        <div className="rounded-md border border-warn/40 bg-warn-soft p-3 text-sm" role="note" data-testid="verifier-warning">
          <p className="flex items-center gap-2 font-medium text-warn">
            <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
            {t("warnTitle", { count: turn.untraced.length })}
          </p>
          <p className="mt-1">{t("warnBody")}</p>
          <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-medium tabular-nums">
            {turn.untraced.map((number) => (
              <li key={number}>
                <bdi>{number}</bdi>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {caveats.length ? (
        <ul className="list-disc space-y-1 ps-5 text-sm text-muted-foreground" data-testid="caveats">
          {caveats.map((caveat) => (
            <CaveatLine key={`${caveat.kind}:${caveat.message}`} caveat={caveat} />
          ))}
        </ul>
      ) : null}
      {traced || unchecked || turn.confidence || turn.scripted ? (
        <div className="flex flex-wrap items-center gap-2">
          {traced ? (
            <Chip className="bg-good-soft text-good" testId="verifier-ok">
              <ShieldCheck className="size-3.5" aria-hidden="true" />
              {t("traced", { count: turn.numbersChecked ?? 0 })}
            </Chip>
          ) : null}
          {unchecked ? (
            <Chip className="bg-warn-soft text-warn" testId="verifier-unchecked">
              <CircleHelp className="size-3.5" aria-hidden="true" />
              {t("unchecked")}
            </Chip>
          ) : null}
          {turn.confidence ? (
            <ConfidenceBadge level={turn.confidence.level} sampleSize={turn.confidence.sampleSize ?? undefined} />
          ) : null}
          {turn.scripted ? (
            <Chip className="bg-muted text-foreground" testId="test-model">
              <FlaskConical className="size-3.5" aria-hidden="true" />
              {t("testModel")}
            </Chip>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
