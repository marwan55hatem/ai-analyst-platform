"use client";

import { useMemo, useRef } from "react";

/**
 * Sheet handlers that send focus back to whatever opened it. Radix returns focus to a Trigger,
 * but drawers opened from a list row or from the URL have none, so focus would fall to <body>.
 */
export function useReturnFocus() {
  const opener = useRef<HTMLElement | null>(null);
  return useMemo(
    () => ({
      onOpenAutoFocus: () => {
        opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      },
      onCloseAutoFocus: (event: Event) => {
        const target = opener.current;
        if (target?.isConnected && target !== document.body) {
          event.preventDefault();
          target.focus();
        }
      },
    }),
    [],
  );
}
