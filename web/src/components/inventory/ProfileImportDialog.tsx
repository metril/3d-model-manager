/**
 * Import dialog for slicer profiles (P6 Task 9): either upload a bundle
 * zip (Bambu Studio's exported printer/process/filament presets, parsed
 * server-side by `app.services.slicer_profiles.parse_bundle`) or pull
 * whatever profiles the configured slicer sidecar already carries
 * ("From sidecar", `{from_sidecar: true}`). Admin-only `{path}` import
 * isn't exposed here -- it's a server-local path, not something a browser
 * upload maps onto.
 */
import { useRef, useState, type FormEvent } from "react";

import { ApiError } from "@/api/client";
import { useImportProfiles } from "@/api/slicing";
import { Button } from "@/components/ui/button";
import {
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function ProfileImportDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const importProfiles = useImportProfiles();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function reset() {
    setFile(null);
    setResult(null);
    importProfiles.reset();
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    importProfiles.mutate(
      { bundle: file },
      { onSuccess: (r) => setResult(`Imported ${r.created} new, updated ${r.updated}.`) },
    );
  }

  function handleFromSidecar() {
    importProfiles.mutate(
      { fromSidecar: true },
      { onSuccess: (r) => setResult(`Imported ${r.created} new, updated ${r.updated}.`) },
    );
  }

  return (
    <FormDialog open={open} onOpenChange={handleOpenChange} dirty={file != null && result == null} busy={importProfiles.isPending}>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Import profiles</DialogTitle>
            <DialogDescription>
              Upload a Bambu Studio bundle zip, or pull whatever profiles the configured slicer
              sidecar already has.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4 py-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="profile-bundle">Bundle (.zip)</Label>
              <Input
                id="profile-bundle"
                ref={fileInputRef}
                type="file"
                accept=".zip"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>

            <div className="flex items-center gap-2">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs text-muted-foreground">or</span>
              <div className="h-px flex-1 bg-border" />
            </div>

            <Button type="button" variant="outline" disabled={importProfiles.isPending} onClick={handleFromSidecar}>
              From sidecar
            </Button>

            {result ? <p className="text-sm text-muted-foreground">{result}</p> : null}
            {importProfiles.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {importProfiles.error instanceof ApiError ? importProfiles.error.detail : "Could not import profiles"}
              </p>
            ) : null}
          </div>

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">Cancel</Button>
            </DialogClose>
            <Button type="submit" disabled={!file || importProfiles.isPending}>
              {importProfiles.isPending ? "Importing..." : "Upload bundle"}
            </Button>
          </DialogFooter>
        </form>
    </FormDialog>
  );
}
