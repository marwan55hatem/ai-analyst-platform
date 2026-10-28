import type { ReactNode } from "react";

/** Page title row: heading, optional one-line description and actions at the end. */
export function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 pb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0 space-y-1">
        <h1 className="text-2xl font-semibold text-balance break-words" dir="auto">
          {title}
        </h1>
        {meta ? <div className="text-sm text-muted-foreground">{meta}</div> : null}
        {description ? <p className="max-w-prose text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}
