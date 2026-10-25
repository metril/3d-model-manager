import type { ReactNode } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";

/** App-wide providers that every routed page may rely on (shared by main.tsx and tests). */
export function AppProviders({ children }: { children: ReactNode }) {
  return <TooltipProvider delayDuration={200}>{children}</TooltipProvider>;
}
