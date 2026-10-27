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
import { Grid } from "@react-three/drei";

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

/** The build-plate grid at real mm scale, plus a thin outline of the
 * plate's edge. `plateSize` is the plate's mm side length; `scaleFactor` is
 * the SAME `1 / max(allBox dimensions)` factor `ModelViewer` hands
 * `<Resize>`, so the grid and the model land in the same normalized scene
 * space by construction -- multiplying any raw mm length (the plate size,
 * the 10mm cell, the 50mm section) by it converts that length into scene
 * units. */
export function PlateGrid({ plateSize, scaleFactor }: { plateSize: number; scaleFactor: number }) {
  const size = plateSize * scaleFactor;
  const half = size / 2;

  const edgeGeometry = useMemo(
    () =>
      new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-half, 0, -half),
        new THREE.Vector3(half, 0, -half),
        new THREE.Vector3(half, 0, half),
        new THREE.Vector3(-half, 0, half),
      ]),
    [half],
  );

  // `useGLTF`'s cache aside, this geometry is created fresh by us on every
  // `half` change -- dispose the stale one so resizing the plate doesn't
  // leak a `BufferGeometry` per change.
  useEffect(() => () => edgeGeometry.dispose(), [edgeGeometry]);

  return (
    <>
      {/* 10mm cells, 50mm sections (both converted to scene units via
          `scaleFactor`). `y=-0.002` sits just under `ContactShadows`'
          plane (`y=-0.001` in ModelViewer.tsx) so the two never z-fight. */}
      <Grid
        args={[size, size]}
        cellSize={10 * scaleFactor}
        sectionSize={50 * scaleFactor}
        cellColor={CELL_COLOR}
        sectionColor={SECTION_COLOR}
        cellThickness={0.6}
        sectionThickness={1}
        fadeDistance={4}
        position={[0, -0.002, 0]}
        layers={GRID_LAYER}
      />
      <lineLoop geometry={edgeGeometry} position={[0, -0.002, 0]} layers={GRID_LAYER}>
        <lineBasicMaterial color={EDGE_COLOR} transparent opacity={EDGE_OPACITY} />
      </lineLoop>
    </>
  );
}
