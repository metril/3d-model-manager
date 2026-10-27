/**
 * In-browser GLB viewer (Task 8). Loaded exclusively via
 * `React.lazy(() => import("@/components/viewer/ModelViewer"))` from
 * `ViewerTab` — this is the lazy-chunk boundary that keeps three.js/R3F/drei
 * out of the main bundle (Global Constraints "BUNDLE RULE"), so this module
 * (and anything it imports) must stay a `default export` and must not be
 * imported eagerly anywhere else.
 *
 * Deviation from SPEC "Frontend" viewer paragraph (controller-approved, see
 * `.superpowers/sdd/m2-constraints.md`): no drei `<Stage>` and no
 * `<Environment preset=…>`/`files=…` -- both fetch HDRs from a CDN at
 * runtime and this app must work fully offline/self-hosted. That constraint
 * does NOT rule out image-based lighting generally: `<Environment>` only
 * takes the network-fetching `EnvironmentCube` path `if (files || preset)`
 * (`@react-three/drei/core/Environment.js`); given `<Lightformer>` children
 * and neither prop, it renders those emissive planes into a local cube
 * render target instead -- real IBL, zero network. That's what the rig below
 * uses, plus real directional lights and ACES Filmic tone mapping (now
 * applied in the postprocessing composer, see `scene/Effects.tsx`), so PBR
 * materials (authored assuming IBL) get specular response and the model
 * reads as solid instead of flat and chalky.
 *
 * ARCHITECTURE (B1 "toggle-fix core"): every combinable part stays mounted
 * for as long as it's in `parts`, whether or not its checklist checkbox is
 * checked -- toggling a checkbox only flips a `<group visible>` flag inside
 * `GltfPart`, it never adds/removes that part's subtree. Two library facts
 * forced this design:
 *
 *  1. `<Canvas>` wraps ALL of its children in exactly ONE internal
 *     `<Suspense>`. If a newly-checked part's `useGLTF` suspends, R3F hides
 *     the WHOLE already-committed scene until that one GLB resolves, then
 *     un-hides it -- which re-fires every layout effect underneath,
 *     including `Bounds`'s, and jumps the camera. Checking one more part
 *     must not blank parts that already loaded, so each part gets its OWN
 *     `<Suspense>` (`GltfPart`'s per-part wrapper below), not a shared one.
 *  2. drei's `Center`/`Resize` re-measure in a `useLayoutEffect` whose deps
 *     do NOT include their children -- toggling which parts render would
 *     never trigger a re-measure on its own. `BoundsRefitter` below is the
 *     explicit re-measure path, driven off `loadedCount` (how many parts
 *     have actually finished loading), not off visibility.
 *
 * Visibility toggles are therefore deliberately cheap: `THREE.Box3.
 * expandByObject` (which every `Box3.setFromObject` call -- `Center`,
 * `Resize`, `Bounds`'s default `refresh()` -- goes through) ignores
 * `.visible`, so hidden parts still count toward the combined bounding box
 * that grounds and normalizes the scene (`Center`/`Resize` stay stable
 * across toggles). Camera FRAMING is the exception: `BoundsRefitter` fits
 * the visible parts' world-space union (`getVisibleBox`), not the full
 * assembly -- otherwise 1 checked part of 11 renders tiny inside the whole
 * assembly's frame. Showing/hiding a part still never triggers a refit by
 * itself: `BoundsRefitter` only runs when `loadedCount` changes (a part
 * finishing its GLB load) or `fitSignal` bumps (an explicit Fit), never on
 * a plain checkbox click -- the new visible set is simply what the next fit
 * measures.
 */
import {
  Component,
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import * as THREE from "three";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useOnRelocate } from "@/lib/windowRelocate";
import { createInvalidateInstaller, createRafScheduler } from "@/components/viewer/rafDriver";
import { canFit, createInteractionLod, createPoseGuard, createRefitScheduler } from "@/components/viewer/refitGuard";
import {
  Bounds,
  Center,
  ContactShadows,
  Environment,
  GizmoHelper,
  GizmoViewcube,
  Lightformer,
  OrbitControls,
  OrthographicCamera,
  Resize,
  useBounds,
  useGizmoContext,
  useGLTF,
} from "@react-three/drei";
import { explodeLayout } from "@/components/viewer/explode";
import type { ExplodeMode, PartExtent } from "@/components/viewer/explode";
import type { LightingRig } from "@/components/viewer/lighting";
import {
  sectionPlaneParams,
  type CameraPreset,
  type SceneStats,
  type Shading,
  type ViewerApi,
  type ViewerToolsState,
} from "@/components/viewer/tools";
import type { ViewerPart } from "@/components/viewer/viewable";
import { ViewerEffects } from "@/components/viewer/scene/Effects";
import { CameraLayers, PlateGrid } from "@/components/viewer/scene/PlateGrid";
import {
  AutoRotate,
  CaptureBridge,
  DoubleClickTarget,
  OrbitPresetGuard,
} from "@/components/viewer/scene/helpers";

// A 1-unit box, used as `Resize`'s `box3` while nothing has finished loading
// yet (`allBox` is `null`) -- `Resize` divides by the box's largest
// dimension to compute its scale, and an empty/zero-size box would divide by
// zero or `-Infinity`. Never mutated; shared across renders.
const UNIT_BOX = new THREE.Box3(new THREE.Vector3(-0.5, -0.5, -0.5), new THREE.Vector3(0.5, 0.5, 0.5));

// The no-explode/no-offset default for `GltfPart`'s `position` -- a stable
// module-level constant (rather than a fresh `[0, 0, 0]` literal per render)
// so an unexploded part's `<group>` prop is referentially stable across
// re-renders, same rationale as `UNIT_BOX` above.
const ZERO_OFFSET: [number, number, number] = [0, 0, 0];

function ownedMaterialsOf(mesh: THREE.Mesh): THREE.Material[] {
  return Array.isArray(mesh.material) ? mesh.material : [mesh.material];
}

function forEachMesh(object: THREE.Object3D, fn: (mesh: THREE.Mesh) => void) {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (mesh.isMesh && mesh.material) fn(mesh);
  });
}

// A tiny local class -- same rationale as `ViewerErrorBoundary` in
// `ViewerStage.tsx` (no hook form for error boundaries, no
// `react-error-boundary` dependency). Scoped to ONE part's subtree: a
// corrupt/unparseable GLB now only takes out its own `<group>`, not the
// combined scene every other checked part renders into, and not the whole
// `ModelViewer` (which `ViewerErrorBoundary` still guards for a crash that
// isn't part-scoped, e.g. a lighting/Canvas-level throw). Renders `null` on
// error rather than a placeholder card -- there's no per-part slot in the
// canvas layout to put one, and the part simply not appearing is enough of a
// signal alongside the still-working parts around it.
class PartErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) return null;
    return this.props.children;
  }
}

/**
 * One combinable GLB part. Unifies the old `GltfModel`/`RecoloredModel`
 * split -- every mount now OWNS its materials (clones each mesh's material
 * once, geometry and textures stay shared with drei's `useGLTF` cache) so a
 * `color` can always be applied/cleared imperatively without ever touching
 * the shared cache materials (which would bleed the color into every other
 * mount of the same content-addressed blob). The original material is
 * stashed on the clone's `userData.__source` so clearing a color can restore
 * it exactly instead of needing a second network round-trip or a re-clone.
 *
 * Also measures the part's own native-mm `Box3` and triangle count once, in
 * the same `useMemo` pass, while the clone is still unparented -- reported
 * upward via `onLoaded` so `ModelViewer` can union every loaded part's box
 * into the combined `Resize`/`Bounds` target without each part fighting over
 * its own separate `Center`/`Resize` (which would destroy their relative
 * positions -- see the header comment on the scene graph below).
 */
function GltfPart({
  id,
  url,
  color,
  visible,
  shading,
  plane,
  offset,
  onLoaded,
  registerGroup,
}: {
  id: number;
  url: string;
  color: string | undefined;
  visible: boolean;
  /** Task 5 "inspection tools" + R10 X-ray -- see the owned-materials effect
   * below for what each mode actually sets. */
  shading: Shading;
  /** Task 5 cross-section: a world-space (normalized-scene) clipping plane,
   * or `null` when sectioning is off. Constructed by `ModelViewer` from
   * `tools.section` via `tools.ts`'s `sectionPlaneParams` -- this component
   * only assigns it onto its owned materials, it never computes plane math
   * itself. */
  plane: THREE.Plane | null;
  /** Task 5 explode view: this part's position offset (native mm, see
   * `ModelViewer`'s `offsets` memo for why native mm rather than normalized
   * scene units), applied to the part's own `<group>` so it moves along
   * with the rest of `Resize`'s scaling. `undefined` (not yet in
   * `loadedParts`, e.g. still loading) falls back to `ZERO_OFFSET`. */
  offset?: [number, number, number];
  onLoaded: (id: number, box: THREE.Box3, triangles: number) => void;
  /** Registers this part's outer `<group>` into `ModelViewer`'s per-part
   * group map (null on unmount) so `getVisibleBox` can measure the visible
   * parts' world-space union at fit time. Both `registerGroup` and `id` are
   * stable, so the memoized callback ref below keeps a stable identity --
   * React then only invokes it on actual mount/unmount, not on every
   * render. */
  registerGroup: (id: number, group: THREE.Group | null) => void;
}) {
  const { scene } = useGLTF(url);

  const handleGroupRef = useCallback(
    (group: THREE.Group | null) => registerGroup(id, group),
    [registerGroup, id],
  );

  const { object, box, triangles } = useMemo(() => {
    const cloned = scene.clone(true);
    let triCount = 0;
    forEachMesh(cloned, (mesh) => {
      const clone = (material: THREE.Material) => {
        const owned = material.clone(); // never mutate the shared cache material
        owned.userData.__source = material;
        return owned;
      };
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(clone) : clone(mesh.material);

      const geometry = mesh.geometry;
      const index = geometry.index;
      triCount += index ? index.count / 3 : (geometry.attributes.position?.count ?? 0) / 3;
    });
    // Measured while `cloned` is still unparented, so this is the part's
    // native mm bounding box -- no Resize/Center/group scaling applied yet.
    const nativeBox = new THREE.Box3().setFromObject(cloned);
    return { object: cloned, box: nativeBox, triangles: triCount };
  }, [scene]);

  useLayoutEffect(() => {
    onLoaded(id, box, triangles);
  }, [id, box, triangles, onLoaded]);

  // Dispose the materials WE cloned above when this part is unmounted -- R3F
  // does not auto-dispose externally-created `<primitive>` objects, so
  // without this every mount would leak owned materials. Geometries and
  // textures are NOT disposed here: they're still owned by drei's `useGLTF`
  // cache and may be shared with other mounts of the same url.
  useEffect(() => {
    return () => {
      forEachMesh(object, (mesh) => {
        ownedMaterialsOf(mesh).forEach((material) => material.dispose());
      });
    };
  }, [object]);

  const invalidate = useThree((state) => state.invalidate);

  // A single imperative traversal over the owned materials for every Task 5
  // inspection toggle (recolor/shading/section), so none of them ever
  // re-clones the scene. Clearing `color` restores each material's `.color`
  // from the `__source` stashed above. `shading`/`clippingPlanes` apply
  // unconditionally to every owned material (not gated on `std.color` the
  // way recolor is) -- both are meaningful even on a material with no
  // `.color` property.
  //
  // R10 X-ray reuses the same `__source` clone `color` restore already
  // relies on: entering xray sets `transparent`/`opacity`/`depthWrite`/
  // `side` for a see-through double-sided look, and leaving it (back to
  // "solid" OR "wireframe" -- wireframe wants the material's normal opaque
  // properties too, just with edges drawn) restores all four straight from
  // the untouched source material rather than hand-tracking what they used
  // to be.
  useEffect(() => {
    forEachMesh(object, (mesh) => {
      ownedMaterialsOf(mesh).forEach((material) => {
        const std = material as THREE.MeshStandardMaterial;
        const source = material.userData.__source as THREE.MeshStandardMaterial | undefined;
        if (std.color) {
          if (color) {
            std.color.set(color);
          } else if (source?.color) {
            std.color.copy(source.color);
          }
        }
        std.wireframe = shading === "wireframe";
        if (shading === "xray") {
          material.transparent = true;
          material.opacity = 0.35;
          material.depthWrite = false;
          material.side = THREE.DoubleSide;
        } else if (source) {
          material.transparent = source.transparent;
          material.opacity = source.opacity;
          material.depthWrite = source.depthWrite;
          material.side = source.side;
        }
        material.clippingPlanes = plane ? [plane] : null;
      });
    });
    invalidate();
  }, [object, color, shading, plane, invalidate]);

  return (
    <group ref={handleGroupRef} visible={visible} position={offset ?? ZERO_OFFSET}>
      <primitive object={object} />
    </group>
  );
}

// Tone mapping itself now lives in the postprocessing composer (see
// `scene/Effects.tsx`'s `<ToneMapping mode={ToneMappingMode.ACES_FILMIC}>`)
// -- `EffectComposer` renders the scene into an offscreen target, and three
// only honors `gl.toneMapping` when rendering to the default framebuffer, so
// an imperative `gl.toneMapping` assignment here would be a no-op (worse,
// `EffectComposer` forces `gl.toneMapping = NoToneMapping` on mount, so it'd
// actively fight the composer). `toneMappingExposure` is unaffected by that
// split: it lives on the renderer, not a scene prop, so it can't go through
// `<Canvas gl={{...}}>` (that object is only applied once at construction
// and won't re-apply when the lighting preset changes at runtime), and the
// composer's `ToneMapping` effect reads it via three's uniform upload same
// as the old imperative renderer path did. Setting it imperatively here on
// every `exposure` change (and nudging `invalidate` since the canvas is
// `frameloop="demand"`) is the only way a preset switch actually shows up.
function Exposure({ exposure }: { exposure: number }) {
  const gl = useThree((state) => state.gl);
  const invalidate = useThree((state) => state.invalidate);

  useEffect(() => {
    gl.toneMappingExposure = exposure;
    invalidate();
  }, [gl, exposure, invalidate]);

  return null;
}

/** Re-measures and refits the camera as parts finish loading, or on demand
 * via `fitSignal` -- `Bounds` is mounted without `fit`/`clip`/`observe`
 * (see below), so it never fits on its own, and `Resize`/`Center` don't
 * watch their children either; nothing else would re-frame the camera as a
 * second/third part's GLB arrives. `fitSignal` is `ViewerStage`'s "Fit view"
 * button/`F` key and the ortho toggle's post-swap recovery (see
 * `ViewerStage.tsx`) -- both bump a counter rather than passing a boolean, so
 * two fits in a row (e.g. pressing F twice) each still trigger this effect
 * instead of coalescing into a no-op state-didn't-change skip.
 *
 * Frames the VISIBLE parts, not the whole assembly: `getVisibleBox` (see
 * `ModelViewer`) measures the world-space union of the visible parts' groups
 * at fit time -- with 1 of 11 parts checked, framing the full-assembly union
 * (which `Box3.expandByObject` would produce, since it ignores `.visible`)
 * renders that one part tiny in a huge frame, and a single-part pop-out
 * window opens absurdly zoomed-out. drei's `refresh(object?: Object3D |
 * Box3)` accepts the box directly (`if (isBox3(object)) box.copy(object)` in
 * `@react-three/drei/core/Bounds.js`). Computed INSIDE this layout effect,
 * not during render: React runs layout effects child-first and in sibling
 * order, and this component sits after `Resize` under `Bounds`, so
 * `Center`/`Resize` have already applied their position/scale by the time
 * the box is measured (and `getVisibleBox` bakes them into world space via
 * `updateWorldMatrix(true, true)`, same as drei's own Object3D path).
 * Falls back to the whole-scene measure when nothing visible has loaded
 * (`getVisibleBox` returns null), still guarded by `loadedCount > 0`:
 * fitting before anything has loaded would frame the `UNIT_BOX` fallback.
 *
 * This component is the ONLY fit driver -- `<Bounds>` below is mounted with
 * NO `fit`/`clip`/`observe` props, on purpose. drei's `Bounds` has an
 * internal layout effect (`Bounds.js` ~line 206: `if (observe ||
 * count.current++ === 0) { api.refresh(); if (fit) api.reset().fit(); if
 * (clip) api.clip(); }`, deps `[size, clip, fit, observe, camera,
 * controls]`) that re-runs whenever `state.controls` attaches -- and drei
 * `OrbitControls` registers itself via `makeDefault` in ITS OWN effect,
 * whose timing relative to part loads is a race. When the GLBs come from
 * the browser HTTP cache (every real revisit), parts resolve and this
 * component fits the tight visible box BEFORE the controls register; the
 * controls registration then re-triggered `Bounds`'s internal effect,
 * whose bare `api.refresh()` measured the FULL scene and `reset().fit()`
 * stomped the visible-parts framing with whole-assembly framing. On a cold
 * cache the order flips, which is why it looked correct on the vite dev
 * server and wrong against the container. With those three props removed,
 * that internal effect only ever does a harmless one-time `refresh()` (no
 * fit, no clip) -- do NOT re-add `observe` (or `fit`/`clip`) to `<Bounds>`;
 * it re-opens the race, and only on warm caches, so tests won't catch it.
 *
 * Replacing what `observe`/the controls re-run used to cover, two more
 * refit triggers live here instead, both via `useThree` so they land in
 * this effect's deps: `controls` (re-fit once the controls attach -- the
 * post-attach fit re-frames the visible box with the controls properly
 * targeted, closing the warm-cache race above from the other side) and
 * `size` (canvas resizes -- window resize, panel collapse/expand -- re-fit
 * the VISIBLE box, where `observe` would have framed the full assembly).
 *
 * Does NOT fire on a plain visibility toggle -- toggling a checked part
 * neither adds nor removes it from `loadedParts` nor bumps `fitSignal`, and
 * `getVisibleBox` is identity-stable (fully ref-based, see its definition),
 * so this intentionally does not run then; the NEXT fit (explicit or
 * load-driven) picks up the new visible set. */
/** Pointer-gesture state shared between `OrbitControls` (start/end) and the
 * things that must not run mid-drag (refits) or should degrade (LOD). A plain
 * object, not React state: flipping it must never re-render the canvas tree. */
interface GestureBus {
  active: boolean;
  start: () => void;
  end: () => void;
  subscribe: (fn: (active: boolean) => void) => () => void;
}
function createGestureBus(): GestureBus {
  const listeners = new Set<(active: boolean) => void>();
  const bus: GestureBus = {
    active: false,
    start: () => {
      bus.active = true;
      listeners.forEach((fn) => fn(true));
    },
    end: () => {
      bus.active = false;
      listeners.forEach((fn) => fn(false));
    },
    subscribe: (fn) => {
      listeners.add(fn);
      return () => void listeners.delete(fn);
    },
  };
  return bus;
}

interface OrbitLike {
  target: THREE.Vector3;
  update: () => void;
}

function BoundsRefitter({
  loadedCount,
  fitSignal,
  getVisibleBox,
  bus,
}: {
  loadedCount: number;
  fitSignal: number;
  getVisibleBox: () => THREE.Box3 | null;
  bus: GestureBus;
}) {
  const api = useBounds();
  const controls = useThree((state) => state.controls);
  const size = useThree((state) => state.size);
  const get = useThree((state) => state.get);
  const sizeOk = size.width >= 2 && size.height >= 2;

  // Always-latest closure so the scheduler/effects below don't depend on
  // `api` (re-created whenever `invalidate`/camera/controls change).
  const runRef = useRef<(kind: "now" | "debounced") => void>(() => {});
  runRef.current = (kind) => {
    const { size: sz, camera, controls: ctl, invalidate } = get();
    const box = getVisibleBox();
    if (!canFit(sz, box)) return;
    if (box) api.refresh(box);
    else if (loadedCount > 0) api.refresh();
    else return;
    const measured = api.getSize();
    if (!canFit(sz, measured.box) || !Number.isFinite(measured.distance)) return;
    api.clip();
    const orbit = ctl as unknown as OrbitLike | null;
    const ortho = (camera as THREE.OrthographicCamera).isOrthographicCamera;
    if (kind === "debounced" && !ortho) {
      // Resize fit: snap, don't tween -- a tween writes camera.position every
      // frame and fights OrbitControls' own update() while the panel is dragged.
      const dir = camera.position.clone().sub(measured.center);
      if (dir.lengthSq() < 1e-12) dir.set(1, 1, 1);
      camera.position.copy(measured.center).addScaledVector(dir.normalize(), measured.distance);
      if (orbit) {
        orbit.target.copy(measured.center);
        camera.lookAt(measured.center);
        orbit.update();
      }
      camera.updateMatrixWorld();
      invalidate();
      return;
    }
    api.fit();
    if (orbit && ortho) {
      // Bounds' ortho fit only animates zoom; a remounted OrbitControls would
      // otherwise keep orbiting the origin.
      orbit.target.copy(measured.center);
      orbit.update();
    }
  };

  const [scheduler] = useState(() =>
    createRefitScheduler({
      delay: 150,
      isGestureActive: () => bus.active,
      run: (kind) => runRef.current(kind),
    }),
  );
  useEffect(() => {
    const off = bus.subscribe((active) => {
      if (!active) scheduler.gestureEnd();
    });
    return () => {
      off();
      scheduler.cancel();
    };
  }, [bus, scheduler]);

  // Explicit triggers (load, Fit view, controls attach, panel becoming laid out).
  useLayoutEffect(() => {
    void controls;
    scheduler.request("now");
  }, [loadedCount, fitSignal, controls, sizeOk, scheduler]);
  // Resize: debounced + instant.
  const firstSize = useRef(true);
  useEffect(() => {
    if (firstSize.current) {
      firstSize.current = false;
      return;
    }
    scheduler.request("debounced");
  }, [size.width, size.height, scheduler]);

  // NaN backstop: a zero-size/degenerate fit can poison camera/controls with
  // non-finite values, after which orbit/zoom silently stop working forever.
  const [poseGuard] = useState(() =>
    createPoseGuard(
      () => {
        const { camera, controls: ctl } = get();
        const target = (ctl as unknown as OrbitLike | null)?.target;
        if (!target) return null;
        return {
          position: camera.position.clone(),
          target: target.clone(),
          zoom: (camera as THREE.OrthographicCamera).zoom,
        };
      },
      (pose) => {
        const { camera, controls: ctl, invalidate } = get();
        const orbit = ctl as unknown as OrbitLike;
        camera.position.copy(pose.position);
        orbit.target.copy(pose.target);
        if (Number.isFinite(pose.zoom)) (camera as THREE.OrthographicCamera).zoom = pose.zoom;
        camera.updateProjectionMatrix();
        camera.updateMatrixWorld();
        orbit.update();
        invalidate();
      },
    ),
  );
  useFrame(() => void poseGuard.check(), -0.5);
  return null;
}

/** Interaction LOD: while OrbitControls is mid-gesture drop N8AO and render at
 * dpr 1; restore ~200ms after the gesture and render one frame. Owns its own
 * state so only this leaf re-renders -- never the canvas tree. */
function InteractiveEffects({ ao, bus }: { ao: boolean; bus: GestureBus }) {
  const setDpr = useThree((state) => state.setDpr);
  const invalidate = useThree((state) => state.invalidate);
  const [interacting, setInteracting] = useState(false);
  useEffect(() => {
    const lod = createInteractionLod(200, setInteracting);
    const off = bus.subscribe((active) => (active ? lod.start() : lod.end()));
    return () => {
      off();
      lod.dispose();
    };
  }, [bus]);
  useEffect(() => {
    setDpr(interacting ? 1 : Math.min(window.devicePixelRatio || 1, 2));
    invalidate();
  }, [interacting, setDpr, invalidate]);
  return <ViewerEffects ao={ao && !interacting} />;
}

/** Recovers from WebGL context loss (GPU reset, tab discard, driver hiccup)
 * instead of leaving a permanently frozen/blank canvas. */
function ContextLossGuard() {
  const gl = useThree((state) => state.gl);
  const invalidate = useThree((state) => state.invalidate);
  useEffect(() => {
    const el = gl.domElement;
    const lost = (e: Event) => e.preventDefault(); // allow restoration
    const restored = () => invalidate();
    el.addEventListener("webglcontextlost", lost);
    el.addEventListener("webglcontextrestored", restored);
    return () => {
      el.removeEventListener("webglcontextlost", lost);
      el.removeEventListener("webglcontextrestored", restored);
    };
  }, [gl, invalidate]);
  return null;
}

/** If the pointer capture is lost mid-drag (dockview overlay, window blur) the
 * `pointerup` never reaches OrbitControls and it stays in a drag state. Cancel
 * the gesture explicitly. */
function GestureSafety({ bus }: { bus: GestureBus }) {
  const gl = useThree((state) => state.gl);
  useEffect(() => {
    const el = gl.domElement;
    const win = el.ownerDocument.defaultView ?? window;
    const cancel = () => {
      if (!bus.active) return;
      el.dispatchEvent(new PointerEvent("pointercancel", { pointerId: 1, bubbles: true }));
      if (bus.active) bus.end();
    };
    el.addEventListener("lostpointercapture", cancel);
    win.addEventListener("blur", cancel);
    return () => {
      el.removeEventListener("lostpointercapture", cancel);
      win.removeEventListener("blur", cancel);
    };
  }, [gl, bus]);
  return null;
}

/** Dev-only handle for e2e/perf checks: `window.__tdmmViewer`. */
function DevHook() {
  const get = useThree((state) => state.get);
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const w = window as unknown as { __tdmmViewer?: unknown };
    w.__tdmmViewer = {
      get camera() {
        return get().camera;
      },
      get controls() {
        return get().controls;
      },
      get gl() {
        return get().gl;
      },
      invalidate: () => get().invalidate(),
    };
    return () => {
      delete w.__tdmmViewer;
    };
  }, [get]);
  return null;
}

// World-space direction FROM the model TOWARD the camera for each preset --
// `tweenCamera(direction)` (drei's `GizmoHelper`, see below) reorients the
// camera to sit along `direction` at its current distance from the target,
// exactly like a `GizmoViewcube` face click. Y-up (see `PlateGrid.tsx` /
// `Center top`'s grounding convention), Z the "front" axis -- matching
// `GizmoViewcube`'s own box-geometry face order (+x Right, +y Top, +z
// Front). `iso` normalizes to the same evenly-weighted corner direction the
// default `OrthographicCamera` position (`[1.2, 1.2, 1.2]`) already sits on,
// so picking it lands on the familiar default three-quarter framing.
const CAMERA_PRESET_DIRECTIONS: Record<Exclude<CameraPreset, null>, THREE.Vector3> = {
  iso: new THREE.Vector3(1, 1, 1).normalize(),
  top: new THREE.Vector3(0, 1, 0),
  front: new THREE.Vector3(0, 0, 1),
  side: new THREE.Vector3(1, 0, 0),
};

// How long after starting a preset's orientation tween (drei's `GizmoHelper`
// animates at `turnRate = 2*PI rad/s`, so even a 180° turn finishes well
// inside this) to also refit distance to the current bounds -- see
// `CameraPresetTween`'s doc comment for why this can't just fire both at
// once.
const CAMERA_PRESET_REFIT_DELAY_MS = 550;

/**
 * R10 camera presets: reorients the camera along `preset`'s direction using
 * the SAME `tweenCamera` the `GizmoViewcube`'s face/edge/corner clicks
 * already use (`useGizmoContext`, only available from inside `<GizmoHelper>`
 * -- this must be mounted as its child, alongside `GizmoViewcube` below).
 * That tween preserves the camera's CURRENT distance from the target and
 * only changes orientation, so it's paired with a follow-up bump of
 * `onSettled` (wired to `BoundsRefitter`'s `fitSignal` by the caller) that
 * refits the distance for the new orientation via the exact same
 * refit machinery every other "Fit view" trigger uses -- reusing rather
 * than re-deriving that fit math.
 *
 * The two can't run concurrently: `GizmoHelper`'s tween writes
 * `camera.position` every frame from its own quaternion slerp, and
 * `Bounds`'s `fit()` animation writes it every frame too, from a SEPARATE
 * origin/goal interpolation captured from wherever the camera was when
 * `fit()` was called -- running both at once would have them fight over
 * the same property every frame. `onSettled` fires on a delay instead of a
 * real "tween finished" callback (`GizmoHelper` doesn't expose one), long
 * enough for the largest possible reorientation (up to 180°) to finish.
 */
function CameraPresetTween({ preset, onSettled }: { preset: CameraPreset; onSettled: () => void }) {
  const context = useGizmoContext() as { tweenCamera?: (direction: THREE.Vector3) => void };
  const lastPreset = useRef<CameraPreset>(null);

  useEffect(() => {
    if (preset && preset !== lastPreset.current) {
      context.tweenCamera?.(CAMERA_PRESET_DIRECTIONS[preset]);
      const id = window.setTimeout(onSettled, CAMERA_PRESET_REFIT_DELAY_MS);
      lastPreset.current = preset;
      return () => window.clearTimeout(id);
    }
    lastPreset.current = preset;
    return undefined;
  }, [preset, context, onSettled]);

  return null;
}

/**
 * `parts` renders one GLB per entry inside a single shared `<Bounds>` /
 * `<Resize>` / `<Center>` stack so multiple mesh parts (Workstream A
 * "multi-part combined view") appear together as one scene, each keeping its
 * own local coordinates relative to the others. Wrapping each part in its
 * OWN `Center`/`Resize` instead would destroy those relative positions (each
 * part would get re-centered and re-scaled independently) -- there must be
 * exactly one wrapper around the whole group, grounding and normalizing the
 * COMBINED bounding box. Each part carries a stable `id` (the source file
 * id) used as the React key -- two files can resolve to the same `url`
 * (blobs are content-addressed by hash), so keying on `url` would collide;
 * keying on `id` keeps them distinct. An optional `color` (M8 G2) recolors
 * that part non-destructively. `background` replaces the previous
 * hard-coded studio gray -- see `background.ts` for the preset/theme
 * resolution that produces it. `lighting` selects the intensities/exposure/
 * contact-shadow toggle -- see `lighting.ts` for the preset resolution.
 *
 * Every part in `parts` is ALWAYS rendered (see the file header) -- `part.
 * visible` toggles a `<group visible>`, it never mounts/unmounts the part.
 * `loadedParts` tracks the parts that have actually finished loading (keyed
 * by id, pruned when a part disappears from `parts` entirely -- e.g. the
 * underlying file set changed), independent of which are currently visible,
 * so the combined bounding box used by `Resize` (and `Bounds`'s no-visible
 * fallback) stays stable across plain visibility toggles; camera fits frame
 * the visible subset via `getVisibleBox` (see the file header).
 */
export default function ModelViewer({
  parts,
  background,
  lighting,
  tools,
  plateSize,
  onStats,
  fitSignal,
  apiRef,
  onPartLoaded,
  onExplodeModeChange,
  onCameraPresetClear,
}: {
  parts: ViewerPart[];
  background: string;
  lighting: LightingRig;
  tools: ViewerToolsState;
  plateSize: number;
  onStats?: (stats: SceneStats | null) => void;
  /** Bumped by `ViewerStage`'s "Fit view" button/`F` key (and the ortho
   * toggle's post-swap recovery) to force a re-fit outside the normal
   * `loadedCount`-driven path -- see `BoundsRefitter`. */
  fitSignal: number;
  /** Published imperative surface (today: `screenshot`) -- see
   * `scene/helpers.tsx`'s `CaptureBridge`. */
  apiRef?: React.MutableRefObject<ViewerApi | null>;
  /** Task 5 explode view: fires the first time each part finishes loading
   * (including late first loads -- a part checked after the initial eager
   * load, or a newly-added file), so `ViewerStage` can reset a nonzero
   * explode back to 0 rather than leaving the just-arrived part visually
   * detached from the rest of the exploded scene. A no-op during the
   * initial eager load, since `tools.explode` starts at 0. FIRST load only
   * (`seenIds` below): each `GltfPart`'s reporting layout effect can re-run
   * for reasons other than a load (its deps include the `onLoaded` callback
   * itself), and firing this on those re-runs would let the explode reset
   * clobber values the slider just set -- see `onLoaded`'s comment. */
  onPartLoaded?: () => void;
  /** Reports the current explode classification ("explode" real assembly /
   * "separate" overlapping pile / "none" nothing to separate) up to
   * `ViewerStage`, which gates + labels the slider from it. Deduped: fires
   * only when the mode string changes. */
  onExplodeModeChange?: (mode: ExplodeMode) => void;
  /** R10 camera presets: fired by `OrbitPresetGuard` on a real user orbit --
   * `ViewerStage` wires this to `onToolsChange({ cameraPreset: null })` so
   * the segmented control stops showing a preset as selected the moment the
   * camera actually moves off it. */
  onCameraPresetClear?: () => void;
}) {
  const [gestureBus] = useState(createGestureBus);
  // `ViewerStage` passes an inline arrow here (new identity every render);
  // route through a ref so the guard's listener (and the canvas tree) stay stable.
  const presetClearRef = useRef(onCameraPresetClear);
  useEffect(() => {
    presetClearRef.current = onCameraPresetClear;
  });
  const stablePresetClear = useCallback(() => presetClearRef.current?.(), []);
  const [loadedParts, setLoadedParts] = useState<Map<number, { box: THREE.Box3; triangles: number }>>(
    () => new Map(),
  );

  // `onLoaded` below must be identity-STABLE (empty deps), so it reads the
  // latest `onPartLoaded` through a ref instead of closing over the prop.
  // The failure mode this prevents is a feedback loop that made the explode
  // slider unusable: `ViewerStage`'s `onPartLoaded` handler is re-created
  // whenever `tools.explode` changes (it closes over it) -> with
  // `onPartLoaded` in `onLoaded`'s deps, `onLoaded` got a new identity ->
  // every `GltfPart`'s reporting layout effect (deps include `onLoaded`)
  // re-fired -> `onPartLoaded()` -> the reset-if-nonzero logic snapped the
  // explode the user just set straight back to 0.
  const onPartLoadedRef = useRef(onPartLoaded);
  useEffect(() => {
    onPartLoadedRef.current = onPartLoaded;
  });

  // Which part ids have already reported a load -- so `onPartLoaded` only
  // fires on a part's FIRST load, not on the reporting effect's re-runs
  // (see above). A ref, not state: nothing renders from it. Pruned in the
  // parts-pruning effect below so a part that leaves `parts` entirely and
  // later returns (file set changed back) counts as a fresh first load.
  const seenIds = useRef(new Set<number>());

  // Each mounted part's outer `<group>`, keyed by part id -- populated by
  // the stable callback ref `GltfPart` puts on its group (registered on
  // mount, deleted on unmount). A part only commits its group once its GLB
  // has resolved (`useGLTF` suspends until then, and the per-part
  // `<Suspense>` holds the subtree back), so presence in this map already
  // means "loaded" -- no cross-check against `loadedParts` needed. A ref,
  // not state: only read imperatively at fit time by `getVisibleBox`.
  const partGroups = useRef(new Map<number, THREE.Group>());

  const registerPartGroup = useCallback((id: number, group: THREE.Group | null) => {
    if (group) {
      partGroups.current.set(id, group);
    } else {
      partGroups.current.delete(id);
    }
  }, []);

  // The world-space union of every VISIBLE loaded part -- what
  // `BoundsRefitter` frames the camera to, so checking 1 of 11 parts fits
  // that one part instead of the whole assembly's footprint (see its
  // comment). Reads `group.visible` off the live groups rather than closing
  // over the `parts` prop: that keeps this callback fully ref-based and
  // identity-stable (`useCallback([])`), which is what lets it sit in
  // `BoundsRefitter`'s effect deps without visibility toggles re-triggering
  // a fit. `updateWorldMatrix(true, true)` first -- `Center`/`Resize` set
  // position/scale in their layout effects but nothing recomputes
  // `matrixWorld` until the next render otherwise, and drei's own
  // `refresh(Object3D)` path does exactly the same bake. Returns null when
  // nothing visible is loaded (all parts hidden, or nothing loaded yet) --
  // `BoundsRefitter` then falls back to the whole-scene measure.
  const getVisibleBox = useCallback((): THREE.Box3 | null => {
    const union = new THREE.Box3();
    const partBox = new THREE.Box3();
    let any = false;
    for (const group of partGroups.current.values()) {
      if (!group.visible) continue;
      group.updateWorldMatrix(true, true);
      partBox.setFromObject(group);
      if (partBox.isEmpty()) continue;
      union.union(partBox);
      any = true;
    }
    return any ? union : null;
  }, []);

  const onLoaded = useCallback((id: number, box: THREE.Box3, triangles: number) => {
    setLoadedParts((prev) => {
      const existing = prev.get(id);
      if (existing && existing.triangles === triangles && existing.box.equals(box)) return prev;
      const next = new Map(prev);
      next.set(id, { box, triangles });
      return next;
    });
    // Outside the setState updater -- StrictMode double-invokes updaters, and
    // a side effect in there could fire twice (or not at all, if React drops
    // the render). The `seenIds` guard also makes this idempotent per part.
    if (!seenIds.current.has(id)) {
      seenIds.current.add(id);
      onPartLoadedRef.current?.();
    }
  }, []);

  // Prune parts that dropped out of `parts` entirely (not merely hidden) --
  // e.g. the model's file set changed under an already-mounted viewer. Also
  // forgets their `seenIds` entry, so a pruned part re-appearing later fires
  // `onPartLoaded` again as a genuine fresh load.
  useEffect(() => {
    const ids = new Set(parts.map((part) => part.id));
    for (const id of seenIds.current) {
      if (!ids.has(id)) seenIds.current.delete(id);
    }
    setLoadedParts((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const id of next.keys()) {
        if (!ids.has(id)) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [parts]);

  const loadedCount = loadedParts.size;

  const allBox = useMemo(() => {
    if (loadedParts.size === 0) return null;
    const union = new THREE.Box3();
    for (const { box } of loadedParts.values()) union.union(box);
    return union;
  }, [loadedParts]);

  // The SAME normalization factor `<Resize box3=…>` below computes
  // internally (`1 / max(box dimensions)`) -- kept here too so `PlateGrid`
  // can convert its own raw-mm measurements (plate size, cell/section size)
  // into the same normalized scene units `Resize` puts the model into,
  // without either of them reaching into the other's internals. Only
  // meaningful once `allBox` exists; `PlateGrid` is gated on `allBox` below
  // so this value is never rendered from while it's still the 1-unit
  // fallback.
  const s = useMemo(() => {
    const box = allBox ?? UNIT_BOX;
    const size = box.getSize(new THREE.Vector3());
    const maxDimension = Math.max(size.x, size.y, size.z);
    return maxDimension > 0 ? 1 / maxDimension : 1;
  }, [allBox]);

  // Task 5 cross-section: the world-space (normalized-scene) clipping plane
  // every `GltfPart` gets assigned onto its owned materials, or `null` when
  // sectioning is off or nothing has loaded yet (`allBox` null -- `size`
  // would be meaningless). `tools.ts`'s `sectionPlaneParams` does the pure
  // plane math (see its header for why that module stays three.js-free);
  // this is the one place that turns its result into a real `THREE.Plane`.
  const plane = useMemo(() => {
    if (!tools.section.enabled || !allBox) return null;
    const size = allBox.getSize(new THREE.Vector3());
    const { normal, constant } = sectionPlaneParams(tools.section, size, s);
    return new THREE.Plane(new THREE.Vector3(...normal), constant);
  }, [tools.section, allBox, s]);

  // Explode/separate view: each loaded part's `<group>` position offset, in
  // NATIVE mm (see `GltfPart`'s `<group position=…>` -- these apply INSIDE
  // `Center`/`Resize`, which rescales the subtree, so pre-scaling by `s` would
  // double-apply). `explodeLayout` classifies the loaded parts: a real
  // assembly (parts already spread apart) explodes radially from the shared
  // center; an overlapping pile (separate files each centered on their own
  // origin) falls back to a grid so the parts actually separate; fewer than
  // two loaded parts is `mode: "none"` with no offsets. Naturally zero at
  // `tools.explode === 0`, so the resting view is untouched.
  const { mode: explodeMode, offsets } = useMemo(() => {
    const extents: PartExtent[] = [];
    for (const [id, { box }] of loadedParts) {
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      extents.push({ id, center: [center.x, center.y, center.z], size: [size.x, size.y, size.z] });
    }
    return explodeLayout(extents, tools.explode);
  }, [loadedParts, tools.explode]);

  const lastModeRef = useRef<ExplodeMode | null>(null);
  useEffect(() => {
    if (!onExplodeModeChange) return;
    if (lastModeRef.current === explodeMode) return;
    lastModeRef.current = explodeMode;
    onExplodeModeChange(explodeMode);
  }, [explodeMode, onExplodeModeChange]);

  // R10 camera presets: bumped by `CameraPresetTween.onSettled` once a
  // preset's reorientation has had time to finish, so `BoundsRefitter` runs
  // an extra refit for the new orientation without needing its own copy of
  // the fit machinery -- see `CameraPresetTween`'s doc comment for why the
  // two can't just fire together. Folded into `fitSignal` below (added, not
  // replacing it) so either trigger still registers as a distinct change.
  const [presetFitBump, setPresetFitBump] = useState(0);
  const handlePresetSettled = useCallback(() => setPresetFitBump((prev) => prev + 1), []);

  // Reports the combined mm-scale bounding box + triangle count of every
  // currently VISIBLE, loaded part -- "how big is this print?" for
  // `ViewerStage`'s stats overlay chip. Native GLB units are mm (see
  // `viewable.ts`'s header / the backend's `convert.py` rescale), so
  // `loadedParts`' boxes need no further conversion. `null` when nothing
  // visible has finished loading. The ref guard skips the callback when the
  // computed value hasn't actually changed (field-equal, not reference
  // equal) -- `onStats` is expected to feed a `setState`, and calling it
  // with an equivalent-but-new object every render would loop forever.
  const lastStatsRef = useRef<SceneStats | null>(null);
  useEffect(() => {
    if (!onStats) return;

    const union = new THREE.Box3();
    let triangles = 0;
    let any = false;
    for (const part of parts) {
      if (!part.visible) continue;
      const loaded = loadedParts.get(part.id);
      if (!loaded) continue;
      union.union(loaded.box);
      triangles += loaded.triangles;
      any = true;
    }

    let next: SceneStats | null = null;
    if (any) {
      const size = union.getSize(new THREE.Vector3());
      next = { x: size.x, y: size.y, z: size.z, triangles };
    }

    const prev = lastStatsRef.current;
    const unchanged =
      prev === next ||
      (prev !== null &&
        next !== null &&
        prev.x === next.x &&
        prev.y === next.y &&
        prev.z === next.z &&
        prev.triangles === next.triangles);
    if (!unchanged) {
      lastStatsRef.current = next;
      onStats(next);
    }
  }, [parts, loadedParts, onStats]);

  return (
    // `antialias: false` -- the scene now renders offscreen into the
    // postprocessing composer's target (see `ViewerEffects` below), where
    // MSAA can't reach the default framebuffer anyway; SMAA in the composer
    // chain replaces it. `localClippingEnabled: true` is required for
    // `THREE.Material.clippingPlanes` to have any effect -- the cross-section
    // feature (Task 5) assigns a plane onto every owned material via
    // `GltfPart`'s owned-materials effect; without this the renderer would
    // silently ignore it.
    <Canvas
      // Driven by `RafDriver` (demand semantics, but on the rAF of whichever
      // window hosts the canvas) so the viewer keeps rendering in a dock pop-out.
      frameloop="never"
      dpr={[1, 2]}
      gl={{ antialias: false, localClippingEnabled: true }}
      className="h-full w-full"
    >
      {/* FIRST child: swaps `invalidate` in its layout effect, which runs
          before any passive effect below (OrbitControls' connect effect has
          `invalidate` in its deps -- swapping after it would dispose and
          re-connect the controls, killing an in-flight gesture). */}
      <RafDriver />
      <DevHook />
      <ContextLossGuard />
      <GestureSafety bus={gestureBus} />
      <color attach="background" args={[background]} />
      <Exposure exposure={lighting.exposure} />
      {/* Unconditional (not gated on `tools.grid`) -- `layers.enable` is
          idempotent, and the ortho toggle (Task 4) swaps the active camera
          out from under this, so it needs to keep re-running regardless of
          whether the grid happens to be on right now. See PlateGrid.tsx's
          file header for the full layer-trap rationale. */}
      <CameraLayers />

      {/* Swaps in an orthographic default camera when `tools.ortho` is on.
          drei's `OrthographicCamera` restores the previous default camera on
          unmount (its `makeDefault` effect's cleanup resets the store's
          `camera` back), so toggling this off cleanly hands the perspective
          camera back. The camera swap also remounts `OrbitControls` below
          (it re-derives its internal controls instance from the store's
          `camera`), which resets the orbit target -- `ViewerStage`'s ortho
          toggle handler calls `onFit()` right after flipping this to recover
          framing (see its comment). Position/zoom picked for a pleasant
          three-quarter default view; the gizmo/orbit controls take over from
          there. */}
      {tools.ortho && <OrthographicCamera makeDefault position={[1.2, 1.2, 1.2]} zoom={140} />}
      <AutoRotate enabled={tools.autoRotate} />
      <CaptureBridge apiRef={apiRef} />
      <DoubleClickTarget />
      {onCameraPresetClear && <OrbitPresetGuard onUserOrbit={stablePresetClear} />}

      <ambientLight intensity={lighting.ambient} />
      <directionalLight position={[2.5, 4, 2.5]} intensity={lighting.key} />
      <directionalLight position={[-3, 1.5, 2]} intensity={lighting.fill} />
      <directionalLight position={[0, 2, -4]} intensity={lighting.rim} />

      {/* No `preset`/`files` -- see the file header. These planes only ever
          render into a local cube render target, never fetched. */}
      <Environment resolution={256} environmentIntensity={lighting.env}>
        <Lightformer form="rect" intensity={2} position={[0, 4, 2]} scale={[6, 6, 1]} target={[0, 0, 0]} />
        <Lightformer form="rect" intensity={0.8} position={[-4, 1, 1]} scale={[4, 6, 1]} target={[0, 0, 0]} />
        <Lightformer form="circle" intensity={1.2} position={[3, 2, -3]} scale={[3, 3, 1]} target={[0, 0, 0]} />
        <Lightformer
          form="rect"
          intensity={0.4}
          position={[0, -3, 0]}
          scale={[8, 8, 1]}
          rotation-x={Math.PI / 2}
        />
      </Environment>

      {/* `Bounds > Resize > Center` all run their layout effects child-first,
          so this evaluates in the needed order: ground the combined bbox to
          y=0 (`Center top`), normalize its largest dimension to 1
          (`Resize`), then frame the camera to it (`Bounds`, fits driven
          EXCLUSIVELY by `BoundsRefitter` below). Deliberately NO
          `fit`/`clip`/`observe` props here: any of them arms `Bounds`'s
          internal layout effect, which re-runs when `state.controls`
          attaches and would `refresh()` (measuring the FULL scene, hidden
          parts included) + `reset().fit()`, stomping `BoundsRefitter`'s
          visible-parts framing whenever cached GLBs resolve before the
          OrbitControls registration -- a warm-cache-only race, so it looks
          fine in dev and regresses silently in production. See
          `BoundsRefitter`'s doc comment before touching these props; it
          also carries the `controls`/`size` refit triggers that replace
          what `observe` covered. Each part gets its own `<Suspense>` +
          `PartErrorBoundary` (inside the per-part wrapper) instead of one
          shared boundary around the whole map -- see the file header for
          why. */}
      <Bounds margin={1.2}>
        <Resize box3={allBox ?? UNIT_BOX}>
          <Center top cacheKey={loadedCount} disable={loadedCount === 0}>
            {parts.map((part) => (
              <PartErrorBoundary key={part.id}>
                <Suspense fallback={null}>
                  <GltfPart
                    id={part.id}
                    url={part.url}
                    color={part.color}
                    visible={part.visible}
                    shading={tools.shading}
                    plane={plane}
                    offset={offsets.get(part.id)}
                    onLoaded={onLoaded}
                    registerGroup={registerPartGroup}
                  />
                </Suspense>
              </PartErrorBoundary>
            ))}
          </Center>
        </Resize>
        <BoundsRefitter
          loadedCount={loadedCount}
          fitSignal={fitSignal + presetFitBump}
          getVisibleBox={getVisibleBox}
          bus={gestureBus}
        />
      </Bounds>

      {/* OUTSIDE `<Bounds>` deliberately -- `BoundsRefitter`'s no-visible
          fallback (`api.refresh()`) walks `Bounds`'s own children's bounding
          box to frame the camera, and the plate is sized independently of
          the model (`plateSize`, not `allBox`); including it in that subtree
          would inflate/skew the camera fit to the plate instead of the
          model. `scaleFactor={s}` is the same
          factor `<Resize>` above computes from `allBox`, so the grid and the
          model agree on scale by construction (see `s`'s comment). Gated on
          `allBox` (not just `tools.grid`) since `s` is only meaningful once
          a part has actually loaded. */}
      {tools.grid && allBox && <PlateGrid plateSize={plateSize} scaleFactor={s} />}

      {/* A render-target soft shadow, not a shadow map -- `<Canvas>` has no
          `shadows` prop and no light here has `castShadow`. Adding either
          would double up with this and need shadow-acne tuning for no
          visual gain.

          Deliberately NO `key` prop -- this used to be keyed on
          `loadedCount` + the visible id set to force a remount (and so a
          fresh `frames={1}` bake) on every part load/checkbox toggle, but
          drei 10.7.7's `ContactShadows` allocates two `WebGLRenderTarget`s
          plus depth/blur `ShaderMaterial`s imperatively in a `useMemo` with
          NO dispose path (no cleanup effect anywhere in
          `@react-three/drei/core/ContactShadows.js`, and the targets never
          appear in its JSX tree, so R3F can't auto-dispose them either) --
          every remount leaked ~2MB of GPU memory into this deliberately
          long-lived WebGL context, unbounded across checkbox toggles.

          Re-bakes still happen without the key: drei declares its bake
          frame counter as `let count = 0` in the component BODY (same
          file), so ANY re-render of `ContactShadows` resets it and the next
          invalidated frame re-runs the one-shot bake. Every event the key
          used to encode (a part load -> `loadedParts` state change; a
          visibility toggle -> `parts` prop change) re-renders `ModelViewer`
          and therefore this component, and the R3F prop commit invalidates
          a frame -- so the bake re-runs exactly when it must, with zero
          remounts. Stray re-renders re-baking too is harmless (a cheap
          one-shot 512^2 pass). If a drei upgrade ever memoizes that counter
          or moves it into a ref/state (i.e. "fixes" the body-reset quirk we
          intentionally rely on), stale shadows after a toggle are the
          symptom -- solve it with an explicit `frames` bump or a fixed
          upstream API then, NOT by re-adding a `key`. */}
      {lighting.contactShadow && (
        <ContactShadows
          position={[0, -0.001, 0]}
          scale={3}
          far={1.2}
          blur={2.5}
          opacity={0.55}
          resolution={512}
          frames={1}
        />
      )}

      <OrbitControls
        makeDefault
        enablePan
        autoRotate={tools.autoRotate}
        autoRotateSpeed={1.5}
        onStart={gestureBus.start}
        onEnd={gestureBus.end}
      />

      {/* `renderPriority={2}`, NOT drei's default of 1 -- with the
          `EffectComposer` active (`ViewerEffects` below, also a
          `useFrame(priority=1)` render taker), drei's `Hud` (which
          `GizmoHelper` renders into) only re-renders the raw
          un-postprocessed default scene when its OWN `renderPriority === 1`,
          which would race the composer at equal priority. At priority 2 it
          just clears depth and draws the cube on top of the composer's
          already-finished output instead. Face clicks tween the camera
          around the default controls' target and call `invalidate()` per
          step, so this works under `frameloop="demand"` unmodified. */}
      <GizmoHelper alignment="bottom-left" margin={[64, 64]} renderPriority={2}>
        <GizmoViewcube />
        <CameraPresetTween preset={tools.cameraPreset} onSettled={handlePresetSettled} />
      </GizmoHelper>

      {/* Task 5 cross-section: N8AO off while sectioning -- ambient
          occlusion baked from the CLIPPED geometry's depth buffer looks
          wrong (dark halos along the cut plane that don't correspond to any
          real crevice), so this gate wins over the lighting preset's own
          `ao` choice rather than combining with it. */}
      <InteractiveEffects ao={lighting.ao && !tools.section.enabled} bus={gestureBus} />
    </Canvas>
  );
}

/** Replaces R3F's `invalidate` with one that schedules a single `advance` on
 * the rAF of the window currently hosting the canvas (demand-style rendering
 * for `frameloop="never"`), and re-targets when a dock moves the canvas
 * between windows. */
function RafDriver() {
  const set = useThree((state) => state.set);
  const get = useThree((state) => state.get);
  // Created once (gl/advance are stable for the canvas's life); read lazily so
  // nothing here can change identity and re-run the install below.
  const [scheduler] = useState(() =>
    createRafScheduler(
      () => get().gl.domElement.ownerDocument.defaultView ?? window,
      (seconds) => get().advance(seconds, true),
    ),
  );
  const [install] = useState(() => createInvalidateInstaller((invalidate) => set({ invalidate })));

  useLayoutEffect(() => {
    install(scheduler.invalidate);
    scheduler.invalidate();
    return scheduler.cancel;
  }, [scheduler, install]);

  useOnRelocate(scheduler.invalidate);
  return null;
}
