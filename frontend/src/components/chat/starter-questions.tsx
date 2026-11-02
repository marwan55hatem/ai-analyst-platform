"use client";

import { useTranslations } from "next-intl";

import type { StarterKey } from "@/lib/chat/starters";

/** Questions this dataset can answer, in an empty conversation; a click asks one. */
export function StarterQuestions({ keys, onAsk }: { keys: StarterKey[]; onAsk: (question: string) => void }) {
  const t = useTranslations("chat");
  return (
    <section aria-labelledby="starters-heading" className="space-y-2">
      <h3 id="starters-heading" className="text-sm text-muted-foreground">
        {t("empty.starters")}
      </h3>
      <ul className="grid gap-2 sm:grid-cols-2" data-testid="starters">
        {keys.map((key) => (
          <li key={key}>
            <button
              type="button"
              onClick={() => onAsk(t(`starters.${key}`))}
              className="h-full w-full rounded-md border bg-card px-3 py-2 text-start text-sm outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t(`starters.${key}`)}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
