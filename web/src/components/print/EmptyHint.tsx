import type { ReactNode } from "react";

/** Empty state: icon, one line, optional CTA. */
export function EmptyHint({ icon, children, action }: { icon: ReactNode; children: ReactNode; action?: ReactNode }) {
  return (
    <div data-slot="empty-hint" className="flex flex-col items-center gap-2 py-10 text-center text-sm text-muted-foreground">
      <span className="text-muted-foreground/60 [&_svg]:size-8">{icon}</span>
      <p>{children}</p>
      {action}
    </div>
  );
}
