/** Native-<select> stand-in for the Radix Select, shared by vitest suites (`vi.mock("@/components/ui/select", () => import("@/test/selectMock"))`). */
import type { ReactNode } from "react";

export function Select({
  value,
  onValueChange,
  children,
  ...rest
}: { value?: string; onValueChange: (value: string) => void; children?: ReactNode } & Record<string, unknown>) {
  return (
    <select value={value} onChange={(event) => onValueChange(event.target.value)} {...rest}>
      {children}
    </select>
  );
}
export const SelectTrigger = () => null;
export const SelectValue = () => null;
export const SelectContent = ({ children }: { children?: ReactNode }) => <>{children}</>;
export const SelectItem = ({ value, children }: { value: string; children?: ReactNode }) => (
  <option value={value}>{children}</option>
);
