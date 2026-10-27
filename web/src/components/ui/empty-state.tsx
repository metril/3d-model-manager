import type { ComponentType, ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Icon + one line + optional CTA for "nothing here yet" surfaces. */
export function EmptyState({
  icon: Icon,
  title,
  action,
  className,
}: {
  icon: ComponentType<{ className?: string }>;
  title: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-3 py-10 text-center", className)}>
      <Icon className="size-8 text-muted-foreground" aria-hidden="true" />
      <p className="max-w-sm text-sm text-muted-foreground">{title}</p>
      {action}
    </div>
  );
}
