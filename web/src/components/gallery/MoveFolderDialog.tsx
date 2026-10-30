import { useState } from "react";
import { toast } from "sonner";
import { ChevronRightIcon, FolderIcon } from "lucide-react";

import { ApiError } from "@/api/client";
import { useStorageTree } from "@/api/storageTree";
import { canMoveFolder, useMoveFolder } from "@/api/storageMove";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/** "Move to…" picker: browse the folders of the source's own revision and pick a destination. */
export function MoveFolderDialog({ src, onClose }: { src: string | null; onClose: () => void }) {
  return (
    <Dialog open={src != null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">{src ? <Body src={src} onClose={onClose} /> : null}</DialogContent>
    </Dialog>
  );
}

function Body({ src, onClose }: { src: string; onClose: () => void }) {
  const root = src.split("/").slice(0, 2).join("/");
  const [dest, setDest] = useState(root);
  const tree = useStorageTree(dest);
  const move = useMoveFolder();
  const name = src.split("/").at(-1);
  const rel = dest === root ? "(revision root)" : dest.slice(root.length + 1);
  const dirs = (tree.data?.dirs ?? []).filter((d) => d.path !== src && !`${d.path}/`.startsWith(`${src}/`));

  function run() {
    move.mutate(
      { src, dest },
      {
        onSuccess: () => {
          toast.success(`Moved ${name}`);
          onClose();
        },
        onError: (e) => toast.error(e instanceof ApiError ? e.detail : e instanceof Error ? e.message : "Move failed"),
      },
    );
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Move “{name}” to…</DialogTitle>
        <DialogDescription>Folders can move within their own revision. Destination: {rel}</DialogDescription>
      </DialogHeader>
      <div className="max-h-64 space-y-0.5 overflow-y-auto rounded-md border p-1">
        {dest !== root && (
          <button
            type="button"
            className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-muted"
            onClick={() => setDest(dest.split("/").slice(0, -1).join("/"))}
          >
            .. (up)
          </button>
        )}
        {dirs.length === 0 ? (
          <p className="px-2 py-1.5 text-sm text-muted-foreground">No subfolders here.</p>
        ) : (
          dirs.map((d) => (
            <button
              key={d.path}
              type="button"
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-muted"
              onClick={() => setDest(d.path)}
            >
              <FolderIcon className="size-4 text-muted-foreground" />
              <span className="flex-1 truncate">{d.name}</span>
              <ChevronRightIcon className="size-3.5 text-muted-foreground" />
            </button>
          ))
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose} disabled={move.isPending}>
          Cancel
        </Button>
        <Button type="button" onClick={run} disabled={move.isPending || !canMoveFolder(src, dest)}>
          {move.isPending ? "Moving…" : "Move here"}
        </Button>
      </DialogFooter>
    </>
  );
}
