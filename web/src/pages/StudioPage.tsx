import { StudioFrame } from "@/components/studio/StudioFrame";

/** `/slicer`: the streamed OrcaSlicer GUI, full viewport inside the app shell. */
export function StudioPage() {
  return (
    <div className="h-[calc(100svh-7rem)] min-h-96 overflow-hidden rounded-lg border">
      <StudioFrame />
    </div>
  );
}
