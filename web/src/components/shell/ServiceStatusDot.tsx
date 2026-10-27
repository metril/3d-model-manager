import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { useAdminStatus } from "@/api/adminStatus";
import { useAuth } from "@/api/auth";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Admin-only aggregate service health: renders only when something is
 * amber/red, linking to Settings -> Server. */
export function ServiceStatusDot({ collapsed }: { collapsed: boolean }) {
  const { t } = useTranslation();
  const isAdmin = useAuth().can("admin");
  const q = useAdminStatus(isAdmin);
  const overall = q.data?.overall;
  if (!isAdmin || !overall || overall === "ok") return null;
  const label = t(`shell.serviceStatus.${overall}`);
  const link = (
    <Link
      to="/settings"
      search={{ tab: "server" }}
      aria-label={label}
      className={cn(
        "mx-2 mb-2 flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-medium hover:bg-muted",
        collapsed && "mx-1.5 justify-center px-0",
      )}
    >
      <span className={cn("size-2.5 shrink-0 rounded-full", overall === "down" ? "bg-destructive" : "bg-warning")} />
      {!collapsed && <span className="truncate text-muted-foreground">{label}</span>}
    </Link>
  );
  if (!collapsed) return link;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}
