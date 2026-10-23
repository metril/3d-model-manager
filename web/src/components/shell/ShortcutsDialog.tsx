import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// R9-C item 5: the full set of keyboard shortcuts across the app, kept here
// as the single source of truth for the help dialog. Each binding is
// registered independently, next to the state/mutation it acts on -- this
// list is documentation, not the wiring.
const SHORTCUTS: Array<{ keys: string; description: string }> = [
  { keys: "/", description: "Focus the library search" },
  { keys: "Esc", description: "Clear selection / leave select mode" },
  { keys: "A", description: "Select all loaded models (in select mode)" },
  { keys: "Ctrl/Cmd + A", description: "Select all loaded models" },
  { keys: "Delete", description: "Delete the selected models" },
  { keys: "F", description: "Toggle favorite (model page)" },
  { keys: "Shift + F", description: "Toggle fullscreen (3D viewer)" },
  { keys: "Ctrl/Cmd + K", description: "Open the command palette" },
  { keys: "Ctrl/Cmd + B", description: "Collapse/expand the sidebar" },
  { keys: "1 – 9", description: "Jump to the first nine sidebar items" },
  { keys: "?", description: "Show this dialog" },
];

/** R9-C item 5: lists every keyboard shortcut in the app. Opened by the `?`
 * hotkey or the sidebar's "Keyboard shortcuts" button. Moved out of
 * `AppShell` in R12 alongside the rest of the studio shell breakout. */
export function ShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>Available anywhere they apply.</DialogDescription>
        </DialogHeader>
        <dl className="space-y-1.5">
          {SHORTCUTS.map((shortcut) => (
            <div key={shortcut.keys} className="flex items-center justify-between gap-4 text-sm">
              <dt className="text-muted-foreground">{shortcut.description}</dt>
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
