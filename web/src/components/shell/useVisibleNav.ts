import { useAuth } from "@/api/auth";
import { useExternalLinks } from "@/api/externalLinks";
import { useFeatures } from "@/api/features";
import { useFinanceConfig } from "@/api/finance";
import { usePrinters } from "@/api/printers";
import {
  NAV_GROUPS,
  SYSTEM_ITEMS,
  externalNavItems,
  filterNavGroups,
  type NavGroup,
  type NavItem,
} from "@/components/shell/navItems";

/** The nav the current user actually sees -- shared by the sidebar and the
 * 1-9 jump shortcuts so the two can never disagree. `ordered` is the
 * sidebar's top-to-bottom order: groups, then external links, then System. */
export function useVisibleNav(): {
  groups: NavGroup[];
  systemItems: NavItem[];
  externalItems: NavItem[];
  ordered: NavItem[];
} {
  const { can } = useAuth();
  const features = useFeatures();
  const financeConfig = useFinanceConfig();
  const externalLinks = useExternalLinks();
  const printerEnabled = !!features.data?.printer_enabled;
  const printers = usePrinters({ enabled: printerEnabled });

  // Finance (P7): a manager always, everyone else only once billing is on.
  const canSeeFinance = can("billing:manage") || !!financeConfig.data?.billing_enabled;
  const systemItems = SYSTEM_ITEMS.filter((item) => !item.billingGated || canSeeFinance);

  const groups = filterNavGroups(NAV_GROUPS, {
    printerEnabled,
    can,
    hasPrinter: (printers.data?.length ?? 0) > 0,
  });
  const externalItems = externalNavItems(externalLinks.data);

  return {
    groups,
    systemItems,
    externalItems,
    ordered: [...groups.flatMap((group) => group.items), ...externalItems, ...systemItems],
  };
}
