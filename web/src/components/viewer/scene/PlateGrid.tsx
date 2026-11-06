/**
 * The mm-scale build-plate grid + a thin outline of the plate's edge, and
 * the camera layer wiring that keeps both visible to the main camera while
 * invisible to `ContactShadows`' bake and the default (layer-0) raycaster.
 * Imported only by ModelViewer -- three.js/drei must stay out of the main
 * bundle (Global Constraints "BUNDLE RULE"), same discipline as
 * `scene/Effects.tsx`.
 *
 * LAYER TRAP (verified): `ContactShadows` (in ModelViewer.tsx) bakes by
 * swapping in `scene.overrideMaterial` and rendering the WHOLE scene from
 * its own shadow camera -- if the grid lived on the default layer (0, same
 * as everything else), it would get swept into that bake as a giant dark
 * blob covering the plate. Every object this module renders is therefore
 * pushed onto `GRID_LAYER` (1) via the `layers` prop (R3F resolves a
 * numeric `layers` prop by calling the target's `layers.set(...)`), and
 * `CameraLayers` enables that layer ONLY on the main viewing camera --
 * `ContactShadows`' shadow camera and the layer-0 raycaster used for
 * pointer hit-testing default to layer 0 only, so they never see the grid.
 */
import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { useThree } from "@react-three/fiber";
import { Grid, Html } from "@react-three/drei";
import { useTranslation } from "react-i18next";

import { plateLabel } from "@/components/viewer/plates";
import type { PlateBed } from "@/components/viewer/plates";

/** Every object `PlateGrid` renders lives on this layer -- see the file
 * header for why. */
export const GRID_LAYER = 1;

// Muted, desaturated slate tones that read against every background preset
// (`background.ts`'s studio/white/dark/theme neutrals) without competing
// with the model for attention. `CELL_COLOR` is the faint 10mm grid,
// `SECTION_COLOR` a touch lighter for the 50mm lines (drei's `Grid` has no
// standalone opacity uniform -- the fade/thickness props below are what
// keep it subtle), and `EDGE_COLOR`/`EDGE_OPACITY` draw a low-contrast plate
// boundary rather than a bright frame.
const CELL_COLOR = "#7b879c";
const SECTION_COLOR = "#566176";
const EDGE_COLOR = "#566176";
const EDGE_OPACITY = 0.5;

/** Enables `GRID_LAYER` on the current camera so it renders the grid,
 * re-run whenever the camera object itself changes -- the ortho toggle
 * (Task 4) swaps in a different camera. `layers.enable` is idempotent, so
 * mounting this unconditionally in `ModelViewer`'s `<Canvas>` (rather than
 * only while `tools.grid` is on) is harmless and simpler than gating it. */
export function CameraLayers() {
  const camera = useThree((state) => state.camera);
  useEffect(() => {
    camera.layers.enable(GRID_LAYER);
  }, [camera]);
  return null;
}

/** One rectangular bed: grid + edge loop. `width`/`depth` are mm along X/Z,
 * `center` the bed centre's (x, z) in mm; `scaleFactor` converts every mm
 * length into the normalized scene space `Resize` puts the model in. */
function BedGrid({
  width,
  depth,
  center,
  scaleFactor,
}: {
  width: number;
  depth: number;
  center: [number, number];
  scaleFactor: number;
}) {
  const w = width * scaleFactor;
  const d = depth * scaleFactor;
  const cx = center[0] * scaleFactor;
  const cz = center[1] * scaleFactor;

  const edgeGeometry = useMemo(
    () =>
      new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(cx - w / 2, 0, cz - d / 2),
        new THREE.Vector3(cx + w / 2, 0, cz - d / 2),
        new THREE.Vector3(cx + w / 2, 0, cz + d / 2),
        new THREE.Vector3(cx - w / 2, 0, cz + d / 2),
      ]),
    [cx, cz, w, d],
  );

  // This geometry is created fresh by us on every size change -- dispose the
  // stale one so resizing the plate doesn't leak a `BufferGeometry` per change.
  useEffect(() => () => edgeGeometry.dispose(), [edgeGeometry]);

  return (
    <>
      {/* 10mm cells, 50mm sections (both converted to scene units via
          `scaleFactor`). `y=-0.002` sits just under `ContactShadows`'
          plane (`y=-0.001` in ModelViewer.tsx) so the two never z-fight. */}
      <Grid
        args={[w, d]}
        cellSize={10 * scaleFactor}
        sectionSize={50 * scaleFactor}
        cellColor={CELL_COLOR}
        sectionColor={SECTION_COLOR}
        cellThickness={0.6}
        sectionThickness={1}
        fadeDistance={4}
        position={[cx, -0.002, cz]}
        layers={GRID_LAYER}
      />
      <lineLoop geometry={edgeGeometry} position={[0, -0.002, 0]} layers={GRID_LAYER}>
        <lineBasicMaterial color={EDGE_COLOR} transparent opacity={EDGE_OPACITY} />
      </lineLoop>
    </>
  );
}

/** The build-plate grid at real mm scale, plus a thin outline of the
 * plate's edge. `plateSize` is the plate's mm side length; `scaleFactor` is
 * the SAME `1 / max(allBox dimensions)` factor `ModelViewer` hands
 * `<Resize>`, so the grid and the model land in the same normalized scene
 * space by construction. Centred on the origin (the `Center top` layout). */
export function PlateGrid({ plateSize, scaleFactor }: { plateSize: number; scaleFactor: number }) {
  return <BedGrid width={plateSize} depth={plateSize} center={[0, 0]} scaleFactor={scaleFactor} />;
}

/** One real-sized bed per plate of a plated 3MF (`PlateLayout.beds`), at the
 * layout offsets -- no recentring, the parts sit on them in the same space. */
export function PlateBeds({ beds, scaleFactor }: { beds: PlateBed[]; scaleFactor: number }) {
  const { t } = useTranslation();
  return (
    <>
      {beds.map((bed) => (
        <BedGrid
          key={bed.index}
          width={bed.size[0]}
          depth={bed.size[1]}
          center={[bed.offset[0] + bed.size[0] / 2, bed.offset[2] - bed.size[1] / 2]}
          scaleFactor={scaleFactor}
        />
      ))}
      {beds.length > 1 &&
        beds.map((bed) => (
          <Html
            key={`l${bed.index}`}
            position={[
              (bed.offset[0] + bed.size[0] / 2) * scaleFactor,
              0,
              (bed.offset[2] + bed.size[1] * 0.04) * scaleFactor,
            ]}
            center
            style={{ pointerEvents: "none" }}
          >
            <span className="whitespace-nowrap rounded bg-background/70 px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {plateLabel({ index: bed.index, name: bed.name }, t)}
            </span>
          </Html>
        ))}
    </>
  );
}
