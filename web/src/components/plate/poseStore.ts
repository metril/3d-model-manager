/**
 * Pure pose state for the plate editor: one entry per placed copy, with a small
 * undo/redo stack. Poses follow the server contract (`POST /models/{slug}/combine`
 * `layout: "manual"`): x/y is the part's footprint centre on the bed in mm, the
 * part always sits on the bed, rotation is about Z, scale is uniform.
 */
export interface PlateItem {
  uid: string;
  file_id: number;
  x_mm: number;
  y_mm: number;
  rot_z_deg: number;
  scale: number;
  copy_index: number;
}

export type PoseFields = Pick<PlateItem, "x_mm" | "y_mm" | "rot_z_deg" | "scale">;

export interface PlateState {
  items: PlateItem[];
  selected: string | null;
  past: PlateItem[][];
  future: PlateItem[][];
}

export type PlateAction =
  | { type: "init"; items: PlateItem[] }
  | { type: "replace"; items: PlateItem[] }
  | { type: "select"; uid: string | null }
  | { type: "snapshot" }
  | { type: "patch"; uid: string; fields: Partial<PoseFields>; record?: boolean }
  | { type: "rotateBy"; uid: string; deg: number }
  | { type: "addCopy"; file_id: number }
  | { type: "removeCopy"; uid: string }
  | { type: "removeLast"; file_id: number }
  | { type: "undo" }
  | { type: "redo" };

export const MAX_ITEMS = 100;
export const MIN_SCALE = 0.01;
export const MAX_SCALE = 20;
const HISTORY = 50;
const COPY_OFFSET_MM = 10;

export const initialState: PlateState = { items: [], selected: null, past: [], future: [] };

const round = (v: number, digits = 3) => {
  const f = 10 ** digits;
  return Math.round(v * f) / f;
};

export function clampPose(fields: Partial<PoseFields>): Partial<PoseFields> {
  const out: Partial<PoseFields> = {};
  if (fields.x_mm !== undefined && Number.isFinite(fields.x_mm)) out.x_mm = round(fields.x_mm);
  if (fields.y_mm !== undefined && Number.isFinite(fields.y_mm)) out.y_mm = round(fields.y_mm);
  if (fields.rot_z_deg !== undefined && Number.isFinite(fields.rot_z_deg)) {
    out.rot_z_deg = round((((fields.rot_z_deg + 180) % 360) + 360) % 360 - 180);
  }
  if (fields.scale !== undefined && Number.isFinite(fields.scale)) {
    out.scale = round(Math.min(MAX_SCALE, Math.max(MIN_SCALE, fields.scale)), 4);
  }
  return out;
}

let counter = 0;
export function newUid(): string {
  counter += 1;
  return `p${Date.now().toString(36)}${counter}`;
}

function push(state: PlateState): Pick<PlateState, "past" | "future"> {
  return { past: [...state.past, state.items].slice(-HISTORY), future: [] };
}

export function plateReducer(state: PlateState, action: PlateAction): PlateState {
  switch (action.type) {
    case "init":
      return { items: action.items, selected: null, past: [], future: [] };
    case "replace":
      return { ...state, ...push(state), items: action.items, selected: null };
    case "select":
      return state.selected === action.uid ? state : { ...state, selected: action.uid };
    case "snapshot":
      return { ...state, ...push(state) };
    case "patch": {
      const fields = clampPose(action.fields);
      const items = state.items.map((i) => (i.uid === action.uid ? { ...i, ...fields } : i));
      return action.record === false ? { ...state, items } : { ...state, ...push(state), items };
    }
    case "rotateBy": {
      const item = state.items.find((i) => i.uid === action.uid);
      if (!item) return state;
      return plateReducer(state, {
        type: "patch",
        uid: action.uid,
        fields: { rot_z_deg: item.rot_z_deg + action.deg },
      });
    }
    case "addCopy": {
      if (state.items.length >= MAX_ITEMS) return state;
      const mine = state.items.filter((i) => i.file_id === action.file_id);
      const last = mine[mine.length - 1];
      const next: PlateItem = {
        uid: newUid(),
        file_id: action.file_id,
        x_mm: (last?.x_mm ?? 0) + COPY_OFFSET_MM,
        y_mm: (last?.y_mm ?? 0) + COPY_OFFSET_MM,
        rot_z_deg: last?.rot_z_deg ?? 0,
        scale: last?.scale ?? 1,
        copy_index: mine.reduce((m, i) => Math.max(m, i.copy_index + 1), 0),
      };
      return { ...state, ...push(state), items: [...state.items, next], selected: next.uid };
    }
    case "removeCopy": {
      if (!state.items.some((i) => i.uid === action.uid)) return state;
      return {
        ...state,
        ...push(state),
        items: state.items.filter((i) => i.uid !== action.uid),
        selected: state.selected === action.uid ? null : state.selected,
      };
    }
    case "removeLast": {
      const mine = state.items.filter((i) => i.file_id === action.file_id);
      const last = mine[mine.length - 1];
      return last ? plateReducer(state, { type: "removeCopy", uid: last.uid }) : state;
    }
    case "undo": {
      const prev = state.past[state.past.length - 1];
      if (!prev) return state;
      return {
        items: prev,
        selected: prev.some((i) => i.uid === state.selected) ? state.selected : null,
        past: state.past.slice(0, -1),
        future: [state.items, ...state.future].slice(0, HISTORY),
      };
    }
    case "redo": {
      const [next, ...rest] = state.future;
      if (!next) return state;
      return {
        items: next,
        selected: next.some((i) => i.uid === state.selected) ? state.selected : null,
        past: [...state.past, state.items].slice(-HISTORY),
        future: rest,
      };
    }
  }
}

/** Client-side mirror of `mesh_combine.pose_half_extents` / `check_poses`. */
export interface PartDims {
  w_mm: number;
  d_mm: number;
  h_mm: number;
}

export function halfExtents(dims: PartDims, item: PoseFields): [number, number] {
  const rad = (item.rot_z_deg * Math.PI) / 180;
  const c = Math.abs(Math.cos(rad));
  const s = Math.abs(Math.sin(rad));
  return [(item.scale * (c * dims.w_mm + s * dims.d_mm)) / 2, (item.scale * (s * dims.w_mm + c * dims.d_mm)) / 2];
}

/** Total clearance kept across each bed axis; mirrors `FIT_MARGIN_MM`. */
export const FIT_MARGIN_MM = 1;

export function itemFits(
  dims: PartDims | undefined,
  item: PoseFields,
  bed: { x_mm: number; y_mm: number },
  height?: number | null,
): boolean {
  if (!dims) return true;
  const [hx, hy] = halfExtents(dims, item);
  const m = FIT_MARGIN_MM / 2;
  const tall = height != null && dims.h_mm * item.scale > height + 1e-6;
  return (
    !tall &&
    item.x_mm - hx >= m - 1e-6 &&
    item.y_mm - hy >= m - 1e-6 &&
    item.x_mm + hx <= bed.x_mm - m + 1e-6 &&
    item.y_mm + hy <= bed.y_mm - m + 1e-6
  );
}
