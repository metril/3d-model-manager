import { OrcaSlicerFrame } from "@/components/orcaslicer/OrcaSlicerFrame";
import { EventsProvider } from "@/hooks/useEvents";

/** `/slicer`: the streamed OrcaSlicer GUI, full viewport and outside the app shell, so it
 * can live in its own browser tab or window. It carries its own SSE provider so a session
 * opened from another tab (`orcaslicer.session_started`) refreshes it. */
export function OrcaSlicerPage() {
  return (
    <EventsProvider>
      <div className="h-svh w-screen overflow-hidden">
        <OrcaSlicerFrame />
      </div>
    </EventsProvider>
  );
}
