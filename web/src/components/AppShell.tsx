import { useState } from "react";
import { Outlet, useNavigate } from "@tanstack/react-router";

import { DevModeBanner } from "@/components/shell/DevModeBanner";
import { AppSidebar } from "@/components/shell/AppSidebar";
import { CommandPalette } from "@/components/shell/CommandPalette";
import { ShortcutsDialog } from "@/components/shell/ShortcutsDialog";
import { useVisibleNav } from "@/components/shell/useVisibleNav";
import { TopBar } from "@/components/shell/TopBar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { EventsProvider } from "@/hooks/useEvents";
import { useHotkeys } from "@/hooks/useHotkeys";

/** App-wide layout (R12 studio shell): the SSE provider, the sidebar +
 * topbar chrome, and the routed page in between. Each piece of behavior
 * that used to live here directly -- feature-gated printer nav, the
 * failed-imports badge, the scan chip, theme toggle, logout, username --
 * now lives in `components/shell/*`; this file only wires the two
 * dialogs (shortcuts, command palette) that any of those pieces can open. */
export function AppShell() {
  // R9-C item 5: `?` opens the shortcuts dialog from anywhere in the app --
  // AppShell is mounted on every authenticated route.
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Below `lg` the sidebar is an off-canvas drawer (see `AppSidebar`) --
  // this is the only state it needs from outside itself, opened by the
  // topbar's hamburger button.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const navigate = useNavigate();
  const { ordered } = useVisibleNav();
  // Digits 1-9 jump to the first nine visible sidebar items (the same order
  // the sidebar renders). `useHotkeys` already ignores inputs/contenteditable.
  const digitBindings: Record<string, () => void> = {};
  ordered.slice(0, 9).forEach((item, index) => {
    digitBindings[String(index + 1)] = () => {
      if (document.querySelector('[role="dialog"]')) return;
      if (item.externalUrl) window.open(item.externalUrl, "_blank", "noopener,noreferrer");
      else void navigate({ to: item.to, params: item.params as never });
    };
  });
  useHotkeys({
    ...digitBindings,
    "?": () => setShortcutsOpen(true),
    // Focus the page's global search, when it exposes one (LibraryPage keeps
    // its own `/` for the library search input).
    "/": () => document.querySelector<HTMLElement>("[data-global-search]")?.focus(),
  });

  return (
    <EventsProvider>
      <div className="flex min-h-svh">
        {/* Collapsed icon-rail nav renders Tooltips; Radix throws without a provider. */}
        <TooltipProvider delayDuration={200}>
          <AppSidebar
            onOpenShortcuts={() => setShortcutsOpen(true)}
            mobileOpen={mobileNavOpen}
            onCloseMobile={() => setMobileNavOpen(false)}
          />
        </TooltipProvider>
        {/* `min-w-0` keeps this column (and its content) from being forced
            wider by the sidebar's own width -- the brief's "main content
            should not be constrained by the sidebar width when collapsed"
            is really "don't let a wide child force the layout back open",
            which a flex-1 + min-w-0 column already prevents. */}
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar onOpenPalette={() => setPaletteOpen(true)} onOpenMobileNav={() => setMobileNavOpen(true)} />
          <DevModeBanner />
          <main className="flex-1 overflow-y-auto p-6">
            <Outlet />
          </main>
        </div>
      </div>
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </EventsProvider>
  );
}
