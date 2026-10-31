"use client";

import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import type { AnomalyEvent } from "@/lib/api/types";
import { rankEvents } from "@/lib/charts/trend";
import { useFormat } from "@/lib/format";

export const TOP_EVENTS = 10;

/** Unusual days, most severe first: the top ten, with a way to see the rest. */
export function EventList({ events, onOpen }: { events: readonly AnomalyEvent[]; onOpen: (id: string) => void }) {
  const t = useTranslations("dashboard");
  const tc = useTranslations("charts");
  const fmt = useFormat();
  const [all, setAll] = useState(false);
  const ranked = useMemo(() => rankEvents(events), [events]);
  if (!ranked.length) return null;
  const extra = ranked.length > TOP_EVENTS;
  const shown = all ? ranked : ranked.slice(0, TOP_EVENTS);

  return (
    <div className="space-y-2 pt-2" data-testid="event-list">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-medium">{t("trend.events", { count: ranked.length })}</h3>
        {extra ? (
          <>
            <p className="text-xs text-muted-foreground" data-testid="event-count">
              {t("trend.topEvents", { shown: fmt.int(TOP_EVENTS), total: fmt.int(ranked.length) })}
            </p>
            <Button variant="link" size="sm" className="h-auto p-0" onClick={() => setAll(!all)} data-testid="event-toggle">
              {all
                ? t("trend.showTopEvents", { shown: fmt.int(TOP_EVENTS) })
                : t("trend.showAllEvents", { total: fmt.int(ranked.length) })}
            </Button>
          </>
        ) : null}
      </div>
      <ul className="flex flex-wrap gap-2">
        {shown.map((e) => {
          const label = (
            <>
              <span className={e.kind === "spike" ? "text-good" : "text-bad"} aria-hidden="true">
                {e.kind === "spike" ? "▲" : "▼"}
              </span>
              {fmt.day(e.start)} · {tc(e.kind)}
            </>
          );
          const id = e.insight_id;
          return (
            <li key={`${e.start}-${e.kind}`}>
              {id ? (
                <Button variant="outline" size="sm" onClick={() => onOpen(id)}>
                  {label}
                </Button>
              ) : (
                <span className="inline-flex h-8 items-center gap-1.5 px-2 text-sm">{label}</span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
