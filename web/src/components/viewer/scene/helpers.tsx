/**
 * Small always-mounted scene helpers for the camera/utility layer (Task 4):
 * auto-rotate's demand-mode invalidate loop, the imperative screenshot
 * bridge, and the manual double-click-to-target listener. Imported only by
 * ModelViewer -- three.js/@react-three/fiber must stay out of the main
 * bundle (Global Constraints "BUNDLE RULE"), same discipline as
 * `scene/Effects.tsx` and `scene/PlateGrid.tsx`.
 */
import { useEffect } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import type { ViewerApi } from "@/components/viewer/tools";

/** The minimal shape this module needs from `useThree`'s `controls` --
 * drei's `OrbitControls` (a `three-stdlib` `OrbitControls` under a
 * `<primitive>`) satisfies it. The store types `controls` as a bare
 * `THREE.EventDispatcher | null` since it's generic over any controls
 * implementation, so this narrows it for the one field/method
 * `DoubleClickTarget` actually uses. */
interface TargetControls {
  target: THREE.Vector3;
  update: () => void;
}

/**
 * drei's `OrbitControls` runs its own `controls.update()` inside a
 * `useFrame`, which only fires on invalidated frames under
 * `frameloop="demand"` -- nothing schedules those frames on its own, so
 * `autoRotate` would visibly stall after the very first invalidation. This
 * sustains the loop for as long as `enabled` is true (one `invalidate()`
 * every frame), plus one kick-off `invalidate()` in an effect so the first
 * frame after flipping the toggle on isn't skipped. Renders nothing.
 */
export function AutoRotate({ enabled }: { enabled: boolean }) {
  const invalidate = useThree((state) => state.invalidate);

  useEffect(() => {
    if (enabled) invalidate();
  }, [enabled, invalidate]);

  useFrame(() => {
    if (enabled) invalidate();
  });

  return null;
}

/**
 * Publishes an imperative `{ screenshot }` onto `apiRef` for `ViewerStage`'s
 * toolbar button to call -- there's no prop path from a DOM button click
 * into a `<Canvas>` child otherwise. The canvas has no
 * `preserveDrawingBuffer` (see `ModelViewer.tsx`'s `gl` prop), so the
 * capture has to read the framebuffer in the same task it's rendered:
 * `advance(timestamp)` synchronously runs the whole demand-mode render loop
 * (composer included, per `@react-three/fiber`'s `RootState.advance`), and
 * `gl.domElement.toBlob` right after captures that fully-composited frame
 * rather than a stale/cleared one.
 */
export function CaptureBridge({ apiRef }: { apiRef?: React.MutableRefObject<ViewerApi | null> }) {
  const gl = useThree((state) => state.gl);
  const advance = useThree((state) => state.advance);

  useEffect(() => {
    if (!apiRef) return;

    apiRef.current = {
      screenshot: () =>
        new Promise((resolve) => {
          advance(performance.now() / 1000);
          gl.domElement.toBlob((blob) => resolve(blob), "image/png");
        }),
    };

    return () => {
      apiRef.current = null;
    };
  }, [apiRef, gl, advance]);

  return null;
}

/**
 * Sets the orbit target to whatever's under the pointer on double-click. A
 * manual `dblclick` listener on `gl.domElement` rather than an R3F
 * `onDoubleClick` mesh handler -- the latter would turn on pointer-move
 * raycasting over every mesh in the scene (potentially millions of
 * triangles) just to support one rare double-click gesture. The default
 * `Raycaster` only sees layer 0, so the plate grid (`PlateGrid.tsx`'s
 * `GRID_LAYER`) is automatically excluded from hits without any extra
 * filtering here. Re-binds whenever the camera/controls change (the ortho
 * toggle swaps both), and is removed on cleanup.
 */
export function DoubleClickTarget() {
  const gl = useThree((state) => state.gl);
  const camera = useThree((state) => state.camera);
  const scene = useThree((state) => state.scene);
  const controls = useThree((state) => state.controls) as TargetControls | null;
  const invalidate = useThree((state) => state.invalidate);

  useEffect(() => {
    if (!controls) return;
    // Reassigned to a local so the closure below keeps TS's narrowing --
    // `controls` itself is a `useThree` selector result, and TS won't carry
    // the null-check narrowing from this line into a function declared
    // further down the same closure.
    const activeControls = controls;

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();

    function onDoubleClick(event: MouseEvent) {
      const rect = gl.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(pointer, camera);
      const [hit] = raycaster.intersectObjects(scene.children, true);
      if (!hit) return;

      activeControls.target.copy(hit.point);
      activeControls.update();
      invalidate();
    }

    const element = gl.domElement;
    element.addEventListener("dblclick", onDoubleClick);
    return () => element.removeEventListener("dblclick", onDoubleClick);
  }, [gl, camera, scene, controls, invalidate]);

  return null;
}

/** Minimal shape this module needs from a controls' `EventDispatcher` --
 * `addEventListener`/`removeEventListener` for the `"start"` event, which
 * `three-stdlib`'s `OrbitControls` dispatches on the pointer-down that
 * begins a real drag (see its `handlePointerDown`), never from a
 * programmatic camera move. drei's `Bounds` relies on exactly this same
 * fact for its own drag-hijack guard (`Bounds.js`'s `controls.addEventListener
 * ('start', ...)`. */
interface StartDispatcher {
  addEventListener: (type: "start", listener: () => void) => void;
  removeEventListener: (type: "start", listener: () => void) => void;
}

/**
 * R10 camera presets: "any user orbit clears the preset back to null" (so
 * the segmented control never keeps showing a preset as selected once the
 * camera has actually moved off it). `OrbitControls`' `"start"` event fires
 * only on a genuine pointer-driven drag -- `GizmoHelper`'s `tweenCamera`
 * (what the preset picker itself drives, see `ModelViewer.tsx`'s
 * `CameraPresetTween`) repositions the camera directly frame-by-frame and
 * calls `controls.update()`, neither of which dispatches `"start"` -- so
 * this only ever fires for a real user drag, never for the preset's own
 * tween. Re-binds whenever `controls` changes (the ortho toggle swaps it).
 */
export function OrbitPresetGuard({ onUserOrbit }: { onUserOrbit: () => void }) {
  const controls = useThree((state) => state.controls) as StartDispatcher | null;

  useEffect(() => {
    if (!controls) return;
    controls.addEventListener("start", onUserOrbit);
    return () => controls.removeEventListener("start", onUserOrbit);
  }, [controls, onUserOrbit]);

  return null;
}
