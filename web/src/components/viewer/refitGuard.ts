/** Pure guards + scheduling for camera refits and interaction LOD (see `ModelViewer`). */
export interface BoxLike {
  min: { x: number; y: number; z: number };
  max: { x: number; y: number; z: number };
}
export type Vec3Like = { x: number; y: number; z: number };

export const MIN_FIT_PX = 2;

export const isFiniteVec = (v: Vec3Like) => Number.isFinite(v.x) && Number.isFinite(v.y) && Number.isFinite(v.z);

/** A fit is only safe with a laid-out panel (aspect 0/NaN poisons the camera) and a real box. */
export function canFit(size: { width: number; height: number }, box: BoxLike | null): boolean {
  if (size.width < MIN_FIT_PX || size.height < MIN_FIT_PX) return false;
  if (!box) return true; // caller falls back to the scene measure; the pose guard backstops it
  if (!isFiniteVec(box.min) || !isFiniteVec(box.max)) return false;
  return box.max.x >= box.min.x && box.max.y >= box.min.y && box.max.z >= box.min.z;
}

/**
 * Runs `run` immediately for explicit triggers (`now`) and after `delay` of
 * quiet for resize triggers (`debounced`). Either is held back while a
 * gesture is active and flushed (once) by `gestureEnd`.
 */
export function createRefitScheduler(opts: {
  delay: number;
  isGestureActive: () => boolean;
  run: (kind: "now" | "debounced") => void;
}) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let held: "now" | "debounced" | null = null;
  const clear = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
  const fire = (kind: "now" | "debounced") => {
    if (opts.isGestureActive()) {
      held = held === "now" ? "now" : kind;
      return;
    }
    opts.run(kind);
  };
  return {
    request(kind: "now" | "debounced") {
      clear();
      if (kind === "now") return fire("now");
      timer = setTimeout(() => {
        timer = null;
        fire("debounced");
      }, opts.delay);
    },
    gestureEnd() {
      if (held && !timer) {
        const k = held;
        held = null;
        opts.run(k);
      }
    },
    cancel() {
      clear();
      held = null;
    },
  };
}

/** Remembers the last finite camera pose so a NaN-poisoned camera can be restored. */
export function createPoseGuard<T extends { position: Vec3Like; target: Vec3Like }>(
  snapshot: () => T | null,
  restore: (pose: T) => void,
) {
  let last: T | null = null;
  return {
    /** Call once per frame. Returns true if it had to restore. */
    check(): boolean {
      const cur = snapshot();
      if (!cur) return false;
      if (isFiniteVec(cur.position) && isFiniteVec(cur.target)) {
        last = cur;
        return false;
      }
      if (last) restore(last);
      return true;
    },
  };
}

/** Interaction level-of-detail: `interacting` true from first start until `restoreDelay` after the last end. */
export function createInteractionLod(restoreDelay: number, onChange: (interacting: boolean) => void) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let interacting = false;
  let active = false;
  const set = (v: boolean) => {
    if (interacting !== v) {
      interacting = v;
      onChange(v);
    }
  };
  return {
    start() {
      active = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
      set(true);
    },
    end() {
      active = false;
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        set(false);
      }, restoreDelay);
    },
    isActive: () => active,
    dispose() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}
