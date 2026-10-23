import { WrenchIcon } from "lucide-react";

import { dueCount, useMaintenanceTasks } from "@/api/ops";
import { Badge } from "@/components/ui/badge";

/** Due-count pill for a printer; renders nothing when nothing is due. Exported for Q3's BadgeRow. */
export function MaintenanceBadge({ printerId }: { printerId: number }) {
  const tasks = useMaintenanceTasks(printerId);
  const n = dueCount(tasks.data);
  if (n === 0) return null;
  const overdue = tasks.data?.some((t) => t.status === "overdue");
  return (
    <Badge variant={overdue ? "destructive" : "secondary"} title="Maintenance due">
      <WrenchIcon />
      {n} due
    </Badge>
  );
}
