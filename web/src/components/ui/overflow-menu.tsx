/**
 * The one "more actions" menu used on every row and card: a MoreHorizontal
 * ghost-icon trigger and labeled groups of items. Hidden items and empty
 * groups are skipped; groups are separated by a rule.
 */
import { Fragment, type ComponentProps } from "react";
import { MoreHorizontalIcon, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface OverflowItem {
  id: string;
  label: string;
  icon?: LucideIcon;
  /** Receives the menu event so callers can `preventDefault()` to keep the menu open. */
  onSelect?: (event: Event) => void;
  /** Renders the item as a plain link (e.g. downloads). */
  href?: string;
  /** With `href`: hint the browser to download rather than navigate. */
  download?: boolean;
  disabled?: boolean;
  destructive?: boolean;
  hidden?: boolean;
  /** Accessible name when the visible label is ambiguous (per-row paths). */
  ariaLabel?: string;
  title?: string;
}

export interface OverflowGroup {
  label?: string;
  items: OverflowItem[];
}

export function OverflowMenu({
  groups,
  align = "end",
  ariaLabel,
  triggerClassName,
  size = "icon-sm",
  variant = "ghost",
}: {
  groups: OverflowGroup[];
  align?: "start" | "center" | "end";
  ariaLabel?: string;
  triggerClassName?: string;
  size?: ComponentProps<typeof Button>["size"];
  variant?: ComponentProps<typeof Button>["variant"];
}) {
  const { t } = useTranslation();
  const visible = groups
    .map((g) => ({ ...g, items: g.items.filter((i) => !i.hidden) }))
    .filter((g) => g.items.length > 0);
  if (visible.length === 0) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant={variant} size={size} className={triggerClassName} aria-label={ariaLabel ?? t("common.moreActions")}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align={align} className="min-w-48">
        {visible.map((g, gi) => (
          <Fragment key={g.label ?? gi}>
            {gi > 0 ? <DropdownMenuSeparator /> : null}
            {g.label ? <DropdownMenuLabel className="text-xs text-muted-foreground">{g.label}</DropdownMenuLabel> : null}
            {g.items.map((item) => {
              const Icon = item.icon;
              const body = (
                <>
                  {Icon ? <Icon className="size-4" /> : null}
                  {item.label}
                </>
              );
              const common = {
                "aria-label": item.ariaLabel,
                title: item.title,
                variant: item.destructive ? ("destructive" as const) : ("default" as const),
              };
              return item.href && !item.disabled ? (
                <DropdownMenuItem key={item.id} asChild {...common}>
                  <a href={item.href} download={item.download || undefined}>{body}</a>
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem key={item.id} disabled={item.disabled} onSelect={item.onSelect} {...common}>
                  {body}
                </DropdownMenuItem>
              );
            })}
          </Fragment>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
