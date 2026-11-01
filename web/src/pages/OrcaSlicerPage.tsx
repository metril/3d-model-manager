import { OrcaSlicerFrame } from "@/components/orcaslicer/OrcaSlicerFrame";

/** `/slicer`: the streamed OrcaSlicer GUI, full viewport inside the app shell. */
export function OrcaSlicerPage() {
  return (
    <div className="h-[calc(100svh-7rem)] min-h-96 overflow-hidden rounded-lg border">
      <OrcaSlicerFrame />
    </div>
  );
}
