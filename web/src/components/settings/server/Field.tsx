import type { ReactNode } from "react";

import { SeededHint } from "@/components/settings/server/SeededHint";
import { Label } from "@/components/ui/label";

export function Field({
  id,
  label,
  field,
  seeded,
  className,
  children,
}: {
  id: string;
  label: string;
  field?: string;
  seeded: Record<string, string>;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`flex flex-col gap-1.5 ${className ?? ""}`}>
      <Label htmlFor={id}>{label}</Label>
      {children}
      {field ? <SeededHint field={field} seeded={seeded} /> : null}
    </div>
  );
}
