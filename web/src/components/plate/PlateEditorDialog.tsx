/**
 * Plate editor: arrange copies of mesh files on the printer bed in 3D (move /
 * rotate / scale / copies), see the fit live, and save the layout as a new
 * multi-object 3MF (`POST /models/{slug}/combine` `layout: "manual"`) that
 * slices with `arrange=0`. Fit is mirrored client-side from the same math the
 * server uses (`poseStore.itemFits`); the server re-validates on save.
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  BoxSelectIcon,
  CopyPlusIcon,
  LayoutGridIcon,
  Loader2Icon,
  MinusIcon,
  MoveIcon,
  PlusIcon,
  Redo2Icon,
  RotateCcwIcon,
  RotateCwIcon,
  Rotate3dIcon,
  ScalingIcon,
  ScissorsIcon,
  Trash2Icon,
  Undo2Icon,
  ArrowDownToLineIcon,
  WandSparklesIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import {
  bedFromProfile,
  useAutoArrange,
  useAutoOrient,
  useCombine,
  useFileObjects,
  type CombinePose,
  type CombineResult,
  type CombineRequest,
  type FileObject,
} from "@/api/combine";
import { useProfiles } from "@/api/slicing";
import type { FileOut } from "@/api/types";
import { canArrange } from "@/components/plate/arrangeable";
import { normalizeQuat } from "@/components/plate/orient";
import { capItems, groupItems, seedsMayOverlap } from "@/components/plate/seeds";
import { buildManualRequest, clearLayout, loadLayout, saveLayout } from "@/components/plate/payload";
import {
  initialState,
  itemFits,
  MAX_ITEMS,
  newUid,
  partKey,
  plateReducer,
  type PartDims,
  type PlateItem,
} from "@/components/plate/poseStore";
import type { PlateMode, ScenePart } from "@/components/plate/PlateScene";
import { SliceDialog } from "@/components/slice/SliceDialog";
import { Button } from "@/components/ui/button";
import { DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { isEditableTarget } from "@/hooks/useHotkeys";
import { glbUrl } from "@/components/viewer/viewable";

const PlateScene = lazy(() => import("@/components/plate/PlateScene"));

const COLORS = ["#2dd4bf", "#60a5fa", "#f59e0b", "#f472b6", "#a78bfa", "#34d399", "#fb7185", "#fbbf24"];

function poseToItem(p: CombinePose): PlateItem {
  return {
    uid: newUid(),
    file_id: p.file_id,
    object_index: p.object_index ?? null,
    x_mm: p.x_mm,
    y_mm: p.y_mm,
    rot_z_deg: p.rot_z_deg,
    scale: p.scale,
    copy_index: p.copy_index,
    orient: normalizeQuat(p.orient),
  };
}

const poseDims = (p: CombinePose): PartDims => ({ w_mm: p.w_mm, d_mm: p.d_mm, h_mm: p.h_mm });

/** A split-3MF object placed where it sits on its own plate (bed-local). */
function objectItem(file_id: number, o: FileObject): PlateItem {
  return {
    uid: newUid(),
    file_id,
    object_index: o.index,
    x_mm: o.x_mm,
    y_mm: o.y_mm,
    rot_z_deg: 0,
    scale: 1,
    copy_index: 0,
    orient: [0, 0, 0, 1],
  };
}

const objectDims = (o: FileObject): PartDims => ({ w_mm: o.w_mm, d_mm: o.d_mm, h_mm: o.h_mm });

const baseName = (relPath: string) => relPath.split("/").pop() ?? relPath;

function profileHeight(data: Record<string, unknown> | null | undefined): number | null {
  for (const k of ["printable_height", "max_print_height"]) {
    const v = Number(data?.[k]);
    if (Number.isFinite(v) && v > 0) return v;
  }
  return null;
}

/** Per-row copies input: commits on blur/Enter. */
function CountField({ label, value, onCommit }: { label: string; value: number; onCommit: (n: number) => void }) {
  const [draft, setDraft] = useState(String(value));
  const commit = () => {
    const n = Number(draft);
    if (draft.trim() === "" || !Number.isFinite(n)) setDraft(String(value));
    else {
      if (Math.floor(n) !== value) onCommit(Math.floor(n));
      // Reset so a cap that leaves the count unchanged doesn't keep the typed value.
      setDraft(String(value));
    }
  };
  return (
    <Input
      type="number"
      min={0}
      max={MAX_ITEMS}
      aria-label={label}
      value={draft}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        e.stopPropagation();
      }}
      className="h-7 w-14 px-1 text-center text-xs tabular-nums"
    />
  );
}

export function NumField({
  label,
  value,
  step,
  onFocus,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  onFocus: () => void;
  onChange: (v: number) => void;
}) {
  // Local string draft so partial input ("-", "1.", "") isn't clobbered by
  // the numeric round-trip; cleared on blur to re-sync with the store.
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <label className="grid gap-0.5 text-[10px] text-muted-foreground">
      {label}
      <Input
        type="number"
        step={step}
        aria-label={label}
        value={draft ?? (Number.isFinite(value) ? value : 0)}
        onFocus={onFocus}
        onBlur={() => setDraft(null)}
        onChange={(e) => {
          setDraft(e.target.value);
          const v = Number(e.target.value);
          if (e.target.value !== "" && Number.isFinite(v)) onChange(v);
        }}
        className="h-7 px-1.5 text-xs"
      />
    </label>
  );
}

export function PlateEditorDialog({
  open,
  onOpenChange,
  slug,
  files,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  slug: string;
  files: FileOut[];
}) {
  const { t } = useTranslation();
  const usableFiles = useMemo(() => files.filter(canArrange), [files]);
  const fileIds = useMemo(() => usableFiles.map((f) => f.id), [usableFiles]);
  const profiles = useProfiles("printer");
  const combine = useCombine(slug);
  const auto = useAutoArrange(slug);
  const autoOrient = useAutoOrient(slug);
  const [state, dispatch] = useReducer(plateReducer, initialState);
  const [mode, setMode] = useState<PlateMode>("translate");
  const [freeRotate, setFreeRotate] = useState(false);
  const [orbitLocked, setOrbitLocked] = useState(false);
  const [faceMode, setFaceMode] = useState(false);
  const [orientFirst, setOrientFirst] = useState(false);
  // Per-copy oriented source dims (the scene measures the rotated mesh; the server's numbers seed it).
  const [itemDims, setItemDims] = useState<Map<string, PartDims>>(new Map());
  const [profileId, setProfileId] = useState("");
  const [bedX, setBedX] = useState("256");
  const [bedY, setBedY] = useState("256");
  const [name, setName] = useState("");
  const [dims, setDims] = useState<Map<string, PartDims>>(new Map());
  const [ready, setReady] = useState(false);
  const [sliceFile, setSliceFile] = useState<FileOut | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const objects = useFileObjects(slug, usableFiles, open);
  const splitObjects = objects.byFile; // file id -> build items (only files worth splitting)
  const objectOf = useCallback(
    (i: { file_id: number; object_index?: number | null }) =>
      i.object_index == null ? undefined : splitObjects.get(i.file_id)?.find((o) => o.index === i.object_index),
    [splitObjects],
  );

  const withBed = (profiles.data ?? []).map((p) => ({ profile: p, bed: bedFromProfile(p.data) }));
  const usable = withBed.filter((p) => p.bed !== null);
  const selectedProfile = usable.find((p) => String(p.profile.id) === profileId) ?? null;
  const customX = Number(bedX);
  const customY = Number(bedY);
  const bedSize = useMemo(
    () => selectedProfile?.bed ?? { x_mm: customX, y_mm: customY },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedProfile?.profile.id, selectedProfile?.bed?.x_mm, selectedProfile?.bed?.y_mm, customX, customY],
  );
  const bedValid = bedSize.x_mm > 0 && bedSize.y_mm > 0;
  const height = selectedProfile ? profileHeight(selectedProfile.profile.data) : null;
  const bed = useMemo(
    () => ({ x_mm: bedSize.x_mm || 1, y_mm: bedSize.y_mm || 1, height }),
    [bedSize.x_mm, bedSize.y_mm, height],
  );

  const autoRequest = useCallback(
    (): CombineRequest => ({
      items: usableFiles.filter((f) => !splitObjects.has(f.id)).map((f) => ({ file_id: f.id, copies: 1 })),
      layout: "auto",
      spacing_mm: 5,
      printer_profile_id: selectedProfile ? selectedProfile.profile.id : null,
      bed: selectedProfile ? null : { x_mm: bedSize.x_mm, y_mm: bedSize.y_mm },
    }),
    [usableFiles, splitObjects, selectedProfile, bedSize.x_mm, bedSize.y_mm],
  );

  // Initial layout: the remembered one for this file set, else the server's auto-arrange.
  // Also fetches every file's AABB, so it waits for the bed (and the profile list) once.
  // Undo entries are recorded on the first actual change of an edit/drag, so a
  // focus or click that changes nothing never clears the redo stack.
  const editRecorded = useRef(false);
  const recordOnce = () => {
    const first = !editRecorded.current;
    editRecorded.current = true;
    return first;
  };
  const started = useRef(false);
  const [retryNonce, setRetryNonce] = useState(0);
  useEffect(() => {
    if (!open || started.current || !bedValid || profiles.isLoading || usableFiles.length === 0 || !objects.settled) return;
    started.current = true;
    const objectParts = new Map<string, PartDims>();
    const objectSeeds: PlateItem[] = [];
    for (const [fid, list] of splitObjects) {
      for (const o of list) {
        objectParts.set(partKey({ file_id: fid, object_index: o.index }), objectDims(o));
        objectSeeds.push(objectItem(fid, o));
      }
    }
    // A remembered layout only counts when it still matches how the files split.
    const usableSaved = (saved: PlateItem[] | null) =>
      saved &&
      saved.every((i) =>
        splitObjects.has(i.file_id) ? objectParts.has(partKey(i)) : i.object_index == null,
      )
        ? saved
        : null;
    const wholeCount = usableFiles.filter((f) => !splitObjects.has(f.id)).length;
    const capped = capItems(objectSeeds, MAX_ITEMS - wholeCount);
    if (capped.truncated) toast.warning(t("plate.seedsTruncated", { max: MAX_ITEMS }));
    // Seeds that could overlap go through the server's auto-arrange with the whole-file parts.
    const arrangeSeeds = capped.items.length > 0 && seedsMayOverlap(splitObjects, wholeCount);
    const finish = (failed: boolean, res: { fits?: boolean; parts: { file_id: number; object_index?: number | null; w_mm: number; d_mm: number; h_mm: number }[]; poses: CombinePose[] } | null) => {
      setDims(new Map([...objectParts, ...(res?.parts ?? []).map((p): [string, PartDims] => [partKey(p), p])]));
      const saved = usableSaved(loadLayout(slug, fileIds));
      const fresh = (res?.poses ?? []).map((p) => ({ item: poseToItem(p), dims: poseDims(p) }));
      setItemDims(saved ? new Map() : new Map(fresh.map((f) => [f.item.uid, f.dims])));
      dispatch({ type: "init", items: saved ?? [...fresh.map((f) => f.item), ...(arrangeSeeds && !failed ? [] : capped.items)] });
      if (!saved && arrangeSeeds && res && !res.fits) toast.warning(t("plate.autoNoFit"));
      setReady(true);
    };
    const req = autoRequest();
    if (arrangeSeeds) req.items = [...req.items, ...groupItems(capped.items)];
    if (req.items.length === 0) {
      finish(false, null);
      return;
    }
    auto.mutate(req, {
      onSuccess: (res) => finish(false, res),
      onError: (e) => {
        // Fall back to the remembered/seed layout so the editor opens, and
        // offer a retry of the server arrangement.
        finish(true, null);
        toast.error(e instanceof ApiError ? e.detail : t("plate.planFailed"), {
          action: {
            label: t("common.retry"),
            onClick: () => {
              started.current = false;
              setRetryNonce((n) => n + 1);
            },
          },
        });
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, bedValid, profiles.isLoading, usableFiles.length, objects.settled, retryNonce]);

  useEffect(() => {
    if (open) return;
    started.current = false;
    setReady(false);
    setFaceMode(false);
    dispatch({ type: "init", items: [] });
  }, [open]);

  // Debounced layout persistence (300ms); a pending write is flushed when the
  // editor closes so the last edit isn't lost.
  const pendingSave = useRef<{ dirty: boolean; slug: string; fileIds: number[]; items: PlateItem[] }>({
    dirty: false,
    slug,
    fileIds,
    items: [],
  });
  useEffect(() => {
    if (!ready) return;
    const p = pendingSave.current;
    p.dirty = true;
    p.slug = slug;
    p.fileIds = fileIds;
    p.items = state.items;
    const id = setTimeout(() => {
      saveLayout(slug, fileIds, state.items);
      p.dirty = false;
    }, 300);
    return () => clearTimeout(id);
  }, [ready, slug, fileIds, state.items]);
  useEffect(() => {
    if (!ready) return;
    const p = pendingSave.current;
    return () => {
      if (p.dirty) saveLayout(p.slug, p.fileIds, p.items);
      p.dirty = false;
    };
  }, [ready]);

  const colorOf = (fileId: number, objectIndex?: number | null) =>
    COLORS[(Math.max(0, fileIds.indexOf(fileId)) + (objectIndex ?? 0)) % COLORS.length];
  const parts = useMemo(() => {
    const m = new Map<string, ScenePart>();
    for (const f of usableFiles) {
      const list = splitObjects.get(f.id);
      if (!list) m.set(partKey({ file_id: f.id }), { key: partKey({ file_id: f.id }), url: glbUrl(f), color: colorOf(f.id) });
      else for (const o of list) {
        const key = partKey({ file_id: f.id, object_index: o.index });
        m.set(key, { key, url: o.glb_url, color: colorOf(f.id, o.index) });
      }
    }
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [usableFiles, splitObjects]);
  const fitsByUid = useMemo(
    () => new Map(state.items.map((i) => [i.uid, itemFits(itemDims.get(i.uid) ?? dims.get(partKey(i)), i, bedSize, height)])),
    [state.items, itemDims, dims, bedSize, height],
  );
  const labelOf = (i: PlateItem) => {
    const f = usableFiles.find((x) => x.id === i.file_id);
    const o = objectOf(i);
    return `${o ? o.name : f ? baseName(f.rel_path) : i.file_id} #${i.copy_index + 1}`;
  };
  const offBed = state.items.filter((i) => fitsByUid.get(i.uid) === false);
  const selected = state.items.find((i) => i.uid === state.selected) ?? null;
  const canSave = ready && bedValid && state.items.length > 0 && offBed.length === 0 && !combine.isPending;

  // Pure server plan for `items` (nothing written to the editor state).
  async function planArrange(items: PlateItem[], autoOrientFirst = false) {
    const req = autoRequest();
    req.items = groupItems(items);
    req.auto_orient = autoOrientFirst;
    const res = await auto.mutateAsync(req);
    return { res, poses: res.poses, allFit: res.poses.every((p) => p.fits) };
  }

  function applyPlan(res: CombineResult) {
    setDims((prev) => new Map([...prev, ...res.parts.map((p): [string, PartDims] => [partKey(p), p])]));
    const fresh = res.poses.map((p) => ({ item: { ...poseToItem(p), rot_z_deg: 0 }, dims: poseDims(p) }));
    setItemDims(new Map(fresh.map((f) => [f.item.uid, f.dims])));
    dispatch({ type: "replace", items: fresh.map((f) => f.item) });
  }

  // Server auto-arrange of `items` (replaces the layout; undoable).
  function arrangeItems(items: PlateItem[], autoOrientFirst = false) {
    if (!bedValid || items.length === 0) return;
    planArrange(items, autoOrientFirst).then(
      ({ res }) => {
        applyPlan(res);
        if (!res.fits) toast.warning(t("plate.autoNoFit"));
      },
      (e) => toast.error(e instanceof ApiError ? e.detail : t("plate.planFailed")),
    );
  }

  // Fill the plate with the selected part: largest copy count whose arrangement fits (binary search).
  const [filling, setFilling] = useState(false);
  const openRef = useRef(open);
  openRef.current = open;
  const fillTarget = selected
    ? { file_id: selected.file_id, object_index: selected.object_index ?? null }
    : (() => {
        const keys = new Map(state.items.map((i) => [partKey(i), { file_id: i.file_id, object_index: i.object_index ?? null }]));
        return keys.size === 1 ? [...keys.values()][0]! : null;
      })();
  async function fillPlate() {
    if (!fillTarget || !bedValid || filling) return;
    const key = partKey(fillTarget);
    const mine = state.items.filter((i) => partKey(i) === key).length;
    const other = state.items.length - mine;
    let lo = mine;
    let hi = MAX_ITEMS - other;
    if (hi <= mine) {
      toast.warning(t("plate.fillNone"));
      return;
    }
    setFilling(true);
    try {
      let best: CombineResult | null = null;
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        const trial = plateReducer(state, { type: "setCount", file_id: fillTarget.file_id, object_index: fillTarget.object_index, count: mid });
        const plan = await planArrange(trial.items);
        if (!openRef.current) return;
        if (plan.allFit) {
          best = plan.res;
          lo = mid;
        } else hi = mid - 1;
      }
      if (!best) toast.warning(t("plate.fillNone"));
      else applyPlan(best);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail : t("plate.planFailed"));
    } finally {
      setFilling(false);
    }
  }

  // Scene selection -> its object's row.
  useEffect(() => {
    if (!selected) return;
    const key = partKey(selected);
    const row = [...(rootRef.current?.querySelectorAll<HTMLElement>("[data-part-row]") ?? [])].find((el) => el.dataset.partRow === key);
    row?.scrollIntoView?.({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.uid]);

  const autoArrange = () => arrangeItems(state.items, orientFirst);

  // Auto orient: the selected copy, or every copy when nothing is selected.
  function orientAuto() {
    const targets = selected ? [selected] : state.items;
    if (targets.length === 0) return;
    const unique = new Map(targets.map((i) => [partKey(i), { file_id: i.file_id, object_index: i.object_index ?? null }]));
    autoOrient.mutate([...unique.values()], {
      onSuccess: (res) => {
        const byFile = new Map(res.files.map((f) => [partKey(f), f]));
        dispatch({
          type: "setOrient",
          entries: targets.flatMap((i) => {
            const f = byFile.get(partKey(i));
            return f ? [{ uid: i.uid, orient: f.orient }] : [];
          }),
        });
        setItemDims((prev) => {
          const next = new Map(prev);
          for (const i of targets) {
            const f = byFile.get(partKey(i));
            if (f) next.set(i.uid, f);
          }
          return next;
        });
      },
      onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("plate.orientFailed")),
    });
  }

  // Plate filter for a split file: bring a plate's objects in (at their bed-local spot) or take them out.
  function togglePlate(fileId: number, plate: number) {
    const list = splitObjects.get(fileId) ?? [];
    const onPlate = new Set(list.filter((o) => o.plate === plate).map((o) => o.index));
    const present = state.items.some((i) => i.file_id === fileId && onPlate.has(i.object_index ?? -1));
    if (present) {
      dispatch({
        type: "replace",
        items: state.items.filter((i) => !(i.file_id === fileId && onPlate.has(i.object_index ?? -1))),
      });
      return;
    }
    const capped = capItems(
      list.filter((o) => onPlate.has(o.index)).map((o) => objectItem(fileId, o)),
      MAX_ITEMS - state.items.length,
    );
    if (capped.truncated) toast.warning(t("plate.seedsTruncated", { max: MAX_ITEMS }));
    if (capped.items.length === 0) return;
    // Bed-local spots may be taken: re-pack so nothing overlaps.
    if (state.items.length === 0) dispatch({ type: "replace", items: capped.items });
    else arrangeItems([...state.items, ...capped.items]);
  }

  const onFace = useCallback((uid: string, normal: [number, number, number]) => {
    dispatch({ type: "layOnFace", uid, normal });
    dispatch({ type: "select", uid });
    setFaceMode(false);
  }, []);
  const onSelect = useCallback((uid: string | null) => dispatch({ type: "select", uid }), []);
  const onBegin = useCallback(() => {
    editRecorded.current = false;
  }, []);
  const onChange = useCallback(
    (uid: string, fields: { x_mm: number; y_mm: number; rot_z_deg: number; scale: number }) =>
      dispatch({ type: "patch", uid, fields, record: recordOnce() }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const onDims = useCallback((uid: string, d: PartDims) => {
    setItemDims((prev) => {
      const cur = prev.get(uid);
      const near = (a: number, b: number) => Math.abs(a - b) < 1e-3;
      if (cur && near(cur.w_mm, d.w_mm) && near(cur.d_mm, d.d_mm) && near(cur.h_mm, d.h_mm)) return prev;
      return new Map(prev).set(uid, { w_mm: d.w_mm, d_mm: d.d_mm, h_mm: d.h_mm });
    });
  }, []);

  function save(thenSlice: boolean) {
    combine.mutate(buildManualRequest(state.items, { profileId: selectedProfile?.profile.id ?? null, bed: bedSize }, name), {
      onSuccess: (res) => {
        pendingSave.current.dirty = false;
        clearLayout(slug, fileIds);
        toast.success(t("plate.saved", { name: res.file ? baseName(res.file.rel_path) : "plate" }));
        if (thenSlice && res.file) setSliceFile(res.file);
        else onOpenChange(false);
      },
      onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("plate.saveFailed")),
    });
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (isEditableTarget(e.target)) return;
    if (e.key === "Shift") setFreeRotate(true);
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === "z") {
      e.preventDefault();
      dispatch({ type: e.shiftKey ? "redo" : "undo" });
    } else if ((e.ctrlKey || e.metaKey) && k === "y") {
      e.preventDefault();
      dispatch({ type: "redo" });
    } else if (e.ctrlKey || e.metaKey || e.altKey) {
      return;
    } else if (k === "g") setMode("translate");
    else if (k === "r") setMode("rotate");
    else if (k === "s") setMode("scale");
    else if (k === "f" && state.items.length > 0) setFaceMode((on) => !on);
    else if (k === "escape" && faceMode) {
      e.stopPropagation();
      setFaceMode(false);
    } else if (k === "escape" && state.selected) {
      e.stopPropagation();
      dispatch({ type: "select", uid: null });
    }
    else if ((k === "delete" || k === "backspace") && state.selected) {
      e.preventDefault();
      dispatch({ type: "removeCopy", uid: state.selected });
    }
  }

  const modes: { id: PlateMode; label: string; key: string; Icon: typeof MoveIcon }[] = [
    { id: "translate", label: t("plate.move"), key: "G", Icon: MoveIcon },
    { id: "rotate", label: t("plate.rotate"), key: "R", Icon: Rotate3dIcon },
    { id: "scale", label: t("plate.scale"), key: "S", Icon: ScalingIcon },
  ];

  return (
    <>
      <FormDialog
        open={open && !sliceFile}
        onOpenChange={onOpenChange}
        dirty={state.past.length > 0}
        busy={combine.isPending}
        className="h-[92vh] w-[96vw] max-w-[96vw] grid-rows-[auto_1fr_auto] sm:max-w-[96vw]"
      >
        <DialogHeader>
          <DialogTitle>{t("plate.title")}</DialogTitle>
          <DialogDescription>{t("plate.description")}</DialogDescription>
        </DialogHeader>

        <div
          ref={rootRef}
          tabIndex={-1}
          onKeyDown={onKeyDown}
          onKeyUp={(e) => e.key === "Shift" && setFreeRotate(false)}
          className="grid min-h-0 gap-3 outline-none lg:grid-cols-[1fr_20rem]"
        >
          <div className="relative min-h-[16rem] overflow-hidden rounded-lg border bg-muted/30">
            {usableFiles.length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">{t("plate.noPreview")}</p>
            ) : ready && bedValid ? (
              <Suspense fallback={<p className="p-4 text-sm text-muted-foreground">{t("plate.loading")}</p>}>
                <PlateScene
                  bed={bed}
                  items={state.items}
                  parts={parts}
                  selected={state.selected}
                  fitsByUid={fitsByUid}
                  mode={mode}
                  freeRotate={freeRotate}
                  orbitLocked={orbitLocked}
                  faceMode={faceMode}
                  onDims={onDims}
                  onFace={onFace}
                  onSelect={onSelect}
                  onBegin={onBegin}
                  onChange={onChange}
                  onOrbitLock={setOrbitLocked}
                />
              </Suspense>
            ) : (
              <p className="p-4 text-sm text-muted-foreground">{t("plate.loading")}</p>
            )}

            <div className="absolute top-2 left-2 flex flex-wrap items-center gap-1 rounded-md bg-background/80 p-1 backdrop-blur">
              {modes.map(({ id, label, key, Icon }) => (
                <Button
                  key={id}
                  type="button"
                  size="sm"
                  variant={mode === id ? "default" : "ghost"}
                  aria-pressed={mode === id}
                  title={`${label} (${key})`}
                  onClick={() => setMode(id)}
                >
                  <Icon className="size-4" />
                  {label}
                </Button>
              ))}
              <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.undo")} title="Ctrl+Z" disabled={state.past.length === 0} onClick={() => dispatch({ type: "undo" })}>
                <Undo2Icon className="size-4" />
              </Button>
              <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.redo")} title="Ctrl+Shift+Z" disabled={state.future.length === 0} onClick={() => dispatch({ type: "redo" })}>
                <Redo2Icon className="size-4" />
              </Button>
              <Button type="button" size="sm" variant="ghost" disabled={auto.isPending || filling || !bedValid || !ready} onClick={autoArrange}>
                <WandSparklesIcon className="size-4" />
                {t("plate.autoArrange")}
              </Button>
              <Button type="button" size="sm" variant="ghost" disabled={!fillTarget || filling || auto.isPending || !bedValid || !ready} title={fillTarget ? t("plate.fillHint") : t("plate.fillPick")} onClick={() => void fillPlate()}>
                {filling ? <Loader2Icon className="size-4 animate-spin" /> : <LayoutGridIcon className="size-4" />}
                {t("plate.fill")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={autoOrient.isPending || !ready || state.items.length === 0}
                title={state.items.length === 0 ? t("plate.noParts") : t("plate.autoOrientHint")}
                onClick={orientAuto}
              >
                <BoxSelectIcon className="size-4" />
                {t("plate.autoOrient")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant={faceMode ? "default" : "ghost"}
                aria-pressed={faceMode}
                disabled={!ready || state.items.length === 0}
                title={state.items.length === 0 ? t("plate.noParts") : `${t("plate.layOnFaceHint")} (F)`}
                onClick={() => setFaceMode((on) => !on)}
              >
                <ArrowDownToLineIcon className="size-4" />
                {t("plate.layOnFace")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant={orientFirst ? "default" : "ghost"}
                aria-pressed={orientFirst}
                title={t("plate.orientFirstHint")}
                onClick={() => setOrientFirst((on) => !on)}
              >
                {t("plate.orientFirst")}
              </Button>
              {mode === "rotate" && selected ? (
                <>
                  <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.rotateLeft")} onClick={() => dispatch({ type: "rotateBy", uid: selected.uid, deg: -90 })}>
                    <RotateCcwIcon className="size-4" />
                  </Button>
                  <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.rotateRight")} onClick={() => dispatch({ type: "rotateBy", uid: selected.uid, deg: 90 })}>
                    <RotateCwIcon className="size-4" />
                  </Button>
                </>
              ) : null}
            </div>

            {offBed.length > 0 ? (
              <div role="alert" className="absolute right-2 bottom-2 max-w-[60%] rounded-md bg-destructive/90 px-2 py-1 text-xs text-destructive-foreground">
                {t("plate.offBed")}: {offBed.map(labelOf).join(", ")}
              </div>
            ) : null}
          </div>

          <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
            {usableFiles.flatMap((f) => {
              const list = splitObjects.get(f.id);
              const plates = list ? [...new Set(list.flatMap((o) => (o.plate == null ? [] : [o.plate])))].sort((x, y) => x - y) : [];
              const groups: (FileObject | null)[] = list ? list : [null];
              const rows = groups.map((o) => {
              const mine = state.items.filter((i) => partKey(i) === partKey({ file_id: f.id, object_index: o?.index }));
              const title = o ? o.name || `#${o.index + 1}` : baseName(f.rel_path);
              return (
                <div key={partKey({ file_id: f.id, object_index: o?.index })} data-part-row={partKey({ file_id: f.id, object_index: o?.index })} className={`space-y-1.5 ${mine.length === 0 ? "opacity-60" : ""}`}>
                  <div
                    data-testid="plate-row"
                    onClick={() => mine[0] && dispatch({ type: "select", uid: mine[0].uid })}
                    className={`flex items-center gap-2 rounded-md ${selected && partKey(selected) === partKey({ file_id: f.id, object_index: o?.index }) ? "bg-primary/10 ring-1 ring-primary" : ""}`}
                  >
                    <span className="size-2.5 shrink-0 rounded-sm" style={{ background: colorOf(f.id, o?.index) }} />
                    <span className="min-w-0 flex-1 truncate font-mono text-xs" title={o ? `${baseName(f.rel_path)} / ${o.name}` : f.rel_path}>
                      {title}
                    </span>
                    <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.removeCopy", { name: title })} disabled={mine.length === 0} onClick={(e) => { e.stopPropagation(); dispatch({ type: "removeLast", file_id: f.id, object_index: o?.index }); }}>
                      <MinusIcon className="size-4" />
                    </Button>
                    <CountField
                      key={`${partKey({ file_id: f.id, object_index: o?.index })}:${mine.length}`}
                      label={t("plate.copies", { name: title })}
                      value={mine.length}
                      onCommit={(n) => dispatch({ type: "setCount", file_id: f.id, object_index: o?.index, count: n })}
                    />
                    <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.addCopy", { name: title })} disabled={state.items.length >= MAX_ITEMS} onClick={(e) => { e.stopPropagation(); dispatch({ type: "addCopy", file_id: f.id, object_index: o?.index }); }}>
                      <PlusIcon className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      size="icon-sm"
                      variant="ghost"
                      aria-label={t("plate.duplicatePart", { name: title })}
                      title={t("plate.duplicatePartHint", { name: title })}
                      disabled={mine.length === 0 || state.items.length >= MAX_ITEMS}
                      onClick={(e) => {
                        e.stopPropagation();
                        const src = mine.find((i) => i.uid === state.selected) ?? mine[mine.length - 1];
                        if (src) dispatch({ type: "addCopy", file_id: src.file_id, object_index: src.object_index ?? null });
                      }}
                    >
                      <CopyPlusIcon className="size-4" />
                    </Button>
                    <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.removeAllPart", { name: title })} title={t("plate.removeAllPart", { name: title })} disabled={mine.length === 0} onClick={(e) => { e.stopPropagation(); dispatch({ type: "removeAll", file_id: f.id, object_index: o?.index }); }}>
                      <Trash2Icon className="size-4" />
                    </Button>
                  </div>
                  {mine.map((i) => (
                    <div
                      key={i.uid}
                      data-testid="plate-item"
                      onClick={() => dispatch({ type: "select", uid: i.uid })}
                      className={`rounded-md border p-1.5 ${i.uid === state.selected ? "border-primary bg-primary/5" : ""} ${fitsByUid.get(i.uid) === false ? "border-destructive" : ""}`}
                    >
                      <div className="mb-1 flex items-center justify-between text-xs">
                        <span>#{i.copy_index + 1}</span>
                        <span className="flex gap-1">
                          <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.duplicate")} disabled={state.items.length >= MAX_ITEMS} onClick={(e) => { e.stopPropagation(); dispatch({ type: "addCopy", file_id: i.file_id, object_index: i.object_index ?? null }); }}>
                            <CopyPlusIcon className="size-3.5" />
                          </Button>
                          <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.deleteCopy")} onClick={(e) => { e.stopPropagation(); dispatch({ type: "removeCopy", uid: i.uid }); }}>
                            <Trash2Icon className="size-3.5" />
                          </Button>
                        </span>
                      </div>
                      <div className="grid grid-cols-4 gap-1">
                        <NumField label="X" value={i.x_mm} onFocus={() => (editRecorded.current = false)} onChange={(v) => dispatch({ type: "patch", uid: i.uid, fields: { x_mm: v }, record: recordOnce() })} />
                        <NumField label="Y" value={i.y_mm} onFocus={() => (editRecorded.current = false)} onChange={(v) => dispatch({ type: "patch", uid: i.uid, fields: { y_mm: v }, record: recordOnce() })} />
                        <NumField label={t("plate.rot")} value={i.rot_z_deg} onFocus={() => (editRecorded.current = false)} onChange={(v) => dispatch({ type: "patch", uid: i.uid, fields: { rot_z_deg: v }, record: recordOnce() })} />
                        <NumField label="%" value={Math.round(i.scale * 10000) / 100} onFocus={() => (editRecorded.current = false)} onChange={(v) => dispatch({ type: "patch", uid: i.uid, fields: { scale: v / 100 }, record: recordOnce() })} />
                      </div>
                    </div>
                  ))}
                </div>
              );
              });
              if (!list) return rows;
              return [
                <div key={`plates-${f.id}`} className="space-y-1">
                  <span className="block truncate font-mono text-xs" title={f.rel_path}>
                    {baseName(f.rel_path)}
                  </span>
                  {plates.length > 1 ? (
                    <div className="flex flex-wrap items-center gap-1" role="group" aria-label={t("plate.platesFilter")}>
                      <span className="text-[10px] text-muted-foreground">{t("plate.platesFilter")}</span>
                      {plates.map((pl) => {
                        const on = state.items.some((i) => i.file_id === f.id && list.some((o) => o.plate === pl && o.index === i.object_index));
                        return (
                          <Button key={pl} type="button" size="sm" variant={on ? "default" : "outline"} aria-pressed={on} className="h-6 px-2 text-xs" onClick={() => togglePlate(f.id, pl)}>
                            {pl}
                          </Button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>,
                ...rows,
              ];
            })}
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1">
            <Label htmlFor="plate-printer" className="text-xs">{t("plate.printer")}</Label>
            <select
              id="plate-printer"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
              className="h-8 rounded-lg border border-input bg-transparent ps-2 text-sm"
            >
              <option value="">{t("plate.customBed")}</option>
              {usable.map(({ profile, bed: b }) => (
                <option key={profile.id} value={String(profile.id)}>
                  {profile.name} ({b!.x_mm} × {b!.y_mm} mm)
                </option>
              ))}
            </select>
          </div>
          {selectedProfile ? null : (
            <>
              <div className="grid w-20 gap-1">
                <Label htmlFor="plate-bed-x" className="text-xs">X (mm)</Label>
                <Input id="plate-bed-x" type="number" min={1} value={bedX} onChange={(e) => setBedX(e.target.value)} className="h-8" />
              </div>
              <div className="grid w-20 gap-1">
                <Label htmlFor="plate-bed-y" className="text-xs">Y (mm)</Label>
                <Input id="plate-bed-y" type="number" min={1} value={bedY} onChange={(e) => setBedY(e.target.value)} className="h-8" />
              </div>
            </>
          )}
          <div className="grid min-w-40 flex-1 gap-1">
            <Label htmlFor="plate-name" className="text-xs">{t("plate.name")}</Label>
            <Input id="plate-name" placeholder={t("plate.namePlaceholder")} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} className="h-8" />
          </div>
          <Button type="button" variant="outline" disabled={!canSave} onClick={() => save(false)}>
            {t("plate.save")}
          </Button>
          <Button type="button" disabled={!canSave} onClick={() => save(true)}>
            <ScissorsIcon className="size-4" />
            {t("plate.saveAndSlice")}
          </Button>
        </div>
      </FormDialog>
      {sliceFile ? (
        <SliceDialog
          open
          onOpenChange={(next) => {
            if (!next) {
              setSliceFile(null);
              onOpenChange(false);
            }
          }}
          file={sliceFile}
          modelSlug={slug}
        />
      ) : null}
    </>
  );
}
