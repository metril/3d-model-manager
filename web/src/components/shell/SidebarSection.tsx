import type { ReactNode } from "react";
import { ChevronRightIcon } from "lucide-react";
import { Collapsible } from "radix-ui";
import { useTranslation } from "react-i18next";

import { useAutoExpand, useSectionCollapsed } from "@/components/shell/useSectionCollapsed";
import { cn } from "@/lib/utils";

/** A sidebar group with a collapsible header. In the icon rail (`rail`) there
 * are no headers and everything stays visible. */
export function SidebarSection({
  name,
  label,
  rail,
  active,
  locationKey,
  className,
  children,
}: {
  name: string;
  label: string;
  rail: boolean;
  /** The current route lives inside this section. */
  active: boolean;
  locationKey: string;
  className?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useSectionCollapsed(name);
  useAutoExpand(collapsed, setCollapsed, active, locationKey);

  if (rail) return <div className={cn("flex flex-col gap-1", className)}>{children}</div>;

  return (
    <Collapsible.Root open={!collapsed} onOpenChange={(open) => setCollapsed(!open)} className={className}>
      <Collapsible.Trigger
        className="label-caps group flex w-full items-center gap-1 rounded-md px-2.5 pb-1 text-left hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        title={collapsed ? t("shell.expandSection", { name: label }) : t("shell.collapseSection", { name: label })}
      >
        <ChevronRightIcon
          aria-hidden="true"
          className={cn("size-3 shrink-0 transition-transform motion-reduce:transition-none", !collapsed && "rotate-90")}
        />
        <span className="truncate">{label}</span>
        {collapsed && active ? (
          <>
            <span aria-hidden="true" className="ml-auto size-1.5 shrink-0 rounded-full bg-sidebar-primary" />
            <span className="sr-only">{t("shell.sectionActive")}</span>
          </>
        ) : null}
      </Collapsible.Trigger>
      <Collapsible.Content className="sidebar-collapsible overflow-hidden">
        <div className="flex flex-col gap-1">{children}</div>
      </Collapsible.Content>
    </Collapsible.Root>
  );
}
