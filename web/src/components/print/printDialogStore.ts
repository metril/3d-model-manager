/**
 * Tiny module store: a request to open the model's PrintDialog from outside
 * ModelHeader (e.g. the viewer's "Print plate N" button), pre-seeded with a
 * file and plate. Same style as `components/viewer/plates.ts`.
 */
import { useSyncExternalStore } from "react";

export interface PrintDialogRequest {
  /** null = open the model-level dialog (no pre-picked file). */
  fileId: number | null;
  plate: number | null;
}

let request: PrintDialogRequest | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => void listeners.delete(l);
}

export function openPrint(next: PrintDialogRequest) {
  request = { fileId: next.fileId, plate: next.plate };
  emit();
}

export function closePrint() {
  if (request === null) return;
  request = null;
  emit();
}

export function getPrintRequest(): PrintDialogRequest | null {
  return request;
}

export function usePrintDialogRequest(): PrintDialogRequest | null {
  return useSyncExternalStore(subscribe, () => request);
}
