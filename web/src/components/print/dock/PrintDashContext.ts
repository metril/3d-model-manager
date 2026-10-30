import { createContext, useContext, type ReactNode } from "react";

import type { ArchiveOut, PrinterOverviewItem, QueueEntry } from "@/api/types";
import type { CardSize } from "@/components/print/card/cardSize";
import type { DashGroup, DashSummary } from "@/lib/printerDashboard";
import type { PrintDashSearch } from "@/pages/printerDashSearch";

/** Everything the dashboard panels share, computed once by the page. */
export interface PrintDashValue {
  printers: PrinterOverviewItem[];
  groups: DashGroup[];
  summary: DashSummary;
  search: PrintDashSearch;
  patchSearch: (patch: Partial<PrintDashSearch>) => void;
  size: CardSize;
  setSize: (size: CardSize) => void;
  upNext: QueueEntry[];
  recentRuns: ArchiveOut[];
  /** Extra rail cards (e.g. the review inbox), shown under the queue. */
  railExtras: ReactNode[];
  refetch: () => void;
}

const PrintDashContext = createContext<PrintDashValue | null>(null);
export const PrintDashProvider = PrintDashContext.Provider;

export function usePrintDash(): PrintDashValue {
  const v = useContext(PrintDashContext);
  if (!v) throw new Error("usePrintDash outside PrintDashProvider");
  return v;
}
