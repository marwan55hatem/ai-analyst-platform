import { useId, type ReactElement, type ReactNode } from "react";
import { cloneElement } from "react";

import { Label } from "@/components/ui/label";

/**
 * Label + control + hint + error, wired with aria-describedby / aria-invalid so screen
 * readers announce the hint and the error with the field.
 */
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactElement<Record<string, unknown>>;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {cloneElement(children, {
        id,
        "aria-describedby": describedBy || undefined,
        "aria-invalid": error ? true : undefined,
      })}
      {hint ? (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
      {error ? (
        <div id={errorId} className="text-sm font-medium text-bad">
          {error}
        </div>
      ) : null}
    </div>
  );
}
