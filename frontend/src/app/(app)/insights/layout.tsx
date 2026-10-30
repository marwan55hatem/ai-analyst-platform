import type { ReactNode } from "react";
import { Suspense } from "react";

import { InsightsNav } from "@/components/insights/insights-nav";

export default function InsightsLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <Suspense>
        <InsightsNav />
      </Suspense>
      <Suspense>{children}</Suspense>
    </>
  );
}
