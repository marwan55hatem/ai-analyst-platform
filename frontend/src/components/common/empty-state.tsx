import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Friendly empty state: what this is, why it's empty, and one way forward. */
export function EmptyState({
  icon,
  title,
  body,
  action,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  body?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-3 rounded-lg border border-dashed bg-card px-6 py-10 sm:px-10",
        className,
      )}
    >
      {icon ? (
        <div className="grid size-11 place-items-center rounded-full bg-info-soft text-primary [&_svg]:size-5">
          {icon}
        </div>
      ) : null}
      <h2 className="text-lg font-semibold">{title}</h2>
      {body ? <p className="max-w-prose text-muted-foreground">{body}</p> : null}
      {action ? <div className="pt-1">{action}</div> : null}
    </div>
  );
}
