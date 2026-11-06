import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useTranslation } from "react-i18next";

// R9-C item 5: the full set of keyboard shortcuts across the app, kept here
// as the single source of truth for the help dialog. Each binding is
// registered independently, next to the state/mutation it acts on -- this
// list is documentation, not the wiring.
const SHORTCUTS: Array<{ keys: string; description: string }> = [
  { keys: "/", description: "shortcuts.search" },
  { keys: "Esc", description: "shortcuts.clearSelection" },
  { keys: "A", description: "shortcuts.selectAllMode" },
  { keys: "Ctrl/Cmd + A", description: "shortcuts.selectAll" },
  { keys: "Delete", description: "shortcuts.trash" },
  { keys: "F", description: "shortcuts.favorite" },
  { keys: "Shift + F", description: "shortcuts.fullscreen" },
  { keys: "Shift + C", description: "shortcuts.camera" },
  { keys: "Esc", description: "shortcuts.closeCamera" },
  { keys: "Ctrl/Cmd + K", description: "shortcuts.palette" },
  { keys: "Ctrl/Cmd + B", description: "shortcuts.sidebar" },
  { keys: "1 – 9", description: "shortcuts.jump" },
  { keys: "?", description: "shortcuts.help" },
];

/** R9-C item 5: lists every keyboard shortcut in the app. Opened by the `?`
 * hotkey or the sidebar's "Keyboard shortcuts" button. Moved out of
 * `AppShell` in R12 alongside the rest of the studio shell breakout. */
export function ShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("shell.shortcuts")}</DialogTitle>
          <DialogDescription>{t("shortcuts.desc")}</DialogDescription>
        </DialogHeader>
        <dl className="space-y-1.5">
          {SHORTCUTS.map((shortcut) => (
            <div key={shortcut.keys} className="flex items-center justify-between gap-4 text-sm">
              <dt className="text-muted-foreground">{t(shortcut.description)}</dt>
              <dd>
                <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-xs">
                  {shortcut.keys}
                </kbd>
              </dd>
            </div>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  );
}
