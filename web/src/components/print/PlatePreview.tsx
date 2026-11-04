import { lazy, Suspense, useRef, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { resolveLighting } from "@/components/viewer/lighting";
import { DEFAULT_TOOLS, type ViewerApi } from "@/components/viewer/tools";
import { plateGlbUrl } from "@/components/viewer/plates";

// three.js/R3F are heavy: the preview chunk only loads once a plate is shown.
const ModelViewer = lazy(() => import("@/components/viewer/ModelViewer"));

const LIGHTING = resolveLighting("studio");
const TOOLS = { ...DEFAULT_TOOLS, autoRotate: false };

/** Compact, lazily loaded 3D preview of ONE plate (the shared viewer scene). */
export default function PlatePreview({
  blobHash,
  plate,
  plateSize = 256,
}: {
  blobHash: string;
  plate: number;
  plateSize?: number;
}) {
  const apiRef = useRef<ViewerApi | null>(null);
  const [fit, setFit] = useState(0);
  const url = plateGlbUrl(blobHash, plate);
  return (
    <div className="h-48 w-full overflow-hidden rounded-lg border border-border bg-muted" data-testid="plate-preview">
      <Suspense fallback={<Skeleton className="h-full w-full" />}>
        <ModelViewer
          parts={[{ id: plate, url, color: undefined, visible: true }]}
          background="#1e293b"
          lighting={LIGHTING}
          tools={TOOLS}
          plateSize={plateSize}
          fitSignal={fit}
          apiRef={apiRef}
          onPartLoaded={() => setFit((n) => n + 1)}
          onExplodeModeChange={() => {}}
          onCameraPresetClear={() => {}}
        />
      </Suspense>
    </div>
  );
}
