/**
 * Plate editor: arrange copies of mesh files on the printer bed in 3D (move /
 * rotate / scale / copies), see the fit live, and save the layout as a new
 * multi-object 3MF (`POST /models/{slug}/combine` `layout: "manual"`) that
 * slices with `arrange=0`. Fit is mirrored client-side from the same math the
 * server uses (`poseStore.itemFits`); the server re-validates on save.
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  CopyPlusIcon,
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
  WandSparklesIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { bedFromProfile, useAutoArrange, useCombine, type CombineRequest } from "@/api/combine";
import { useProfiles } from "@/api/slicing";
import type { FileOut } from "@/api/types";
import { canArrange } from "@/components/plate/arrangeable";
import { buildManualRequest, clearLayout, loadLayout, saveLayout } from "@/components/plate/payload";
import {
  initialState,
  itemFits,
  MAX_ITEMS,
  newUid,
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
import { glbUrl } from "@/components/viewer/viewable";

const PlateScene = lazy(() => import("@/components/plate/PlateScene"));

const COLORS = ["#2dd4bf", "#60a5fa", "#f59e0b", "#f472b6", "#a78bfa", "#34d399", "#fb7185", "#fbbf24"];

const baseName = (relPath: string) => relPath.split("/").pop() ?? relPath;

function profileHeight(data: Record<string, unknown> | null | undefined): number | null {
  for (const k of ["printable_height", "max_print_height"]) {
    const v = Number(data?.[k]);
    if (Number.isFinite(v) && v > 0) return v;
  }
  return null;
}

function NumField({
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
  return (
    <label className="grid gap-0.5 text-[10px] text-muted-foreground">
      {label}
      <Input
        type="number"
        step={step}
        aria-label={label}
        value={Number.isFinite(value) ? value : 0}
        onFocus={onFocus}
        onChange={(e) => {
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
  const [state, dispatch] = useReducer(plateReducer, initialState);
  const [mode, setMode] = useState<PlateMode>("translate");
  const [freeRotate, setFreeRotate] = useState(false);
  const [orbitLocked, setOrbitLocked] = useState(false);
  const [profileId, setProfileId] = useState("");
  const [bedX, setBedX] = useState("256");
  const [bedY, setBedY] = useState("256");
  const [name, setName] = useState("");
  const [dims, setDims] = useState<Map<number, PartDims>>(new Map());
  const [ready, setReady] = useState(false);
  const [sliceFile, setSliceFile] = useState<FileOut | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

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
      items: usableFiles.map((f) => ({ file_id: f.id, copies: 1 })),
      layout: "auto",
      spacing_mm: 5,
      printer_profile_id: selectedProfile ? selectedProfile.profile.id : null,
      bed: selectedProfile ? null : { x_mm: bedSize.x_mm, y_mm: bedSize.y_mm },
    }),
    [usableFiles, selectedProfile, bedSize.x_mm, bedSize.y_mm],
  );

  // Initial layout: the remembered one for this file set, else the server's auto-arrange.
  // Also fetches every file's AABB, so it waits for the bed (and the profile list) once.
  const started = useRef(false);
  useEffect(() => {
    if (!open || started.current || !bedValid || profiles.isLoading || usableFiles.length === 0) return;
    started.current = true;
    auto.mutate(autoRequest(), {
      onSuccess: (res) => {
        setDims(new Map(res.parts.map((p) => [p.file_id, p])));
        const saved = loadLayout(slug, fileIds);
        dispatch({
          type: "init",
          items:
            saved ??
            res.poses.map((p) => ({
              uid: newUid(),
              file_id: p.file_id,
              x_mm: p.x_mm,
              y_mm: p.y_mm,
              rot_z_deg: p.rot_z_deg,
              scale: p.scale,
              copy_index: p.copy_index,
            })),
        });
        setReady(true);
      },
      onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("plate.planFailed")),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, bedValid, profiles.isLoading, usableFiles.length]);

  useEffect(() => {
    if (ready) saveLayout(slug, fileIds, state.items);
  }, [ready, slug, fileIds, state.items]);

  const colorOf = (fileId: number) => COLORS[Math.max(0, fileIds.indexOf(fileId)) % COLORS.length];
  const parts = useMemo(
    () => new Map<number, ScenePart>(usableFiles.map((f) => [f.id, { file_id: f.id, url: glbUrl(f), color: colorOf(f.id) }])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [usableFiles],
  );
  const fitsByUid = useMemo(
    () => new Map(state.items.map((i) => [i.uid, itemFits(dims.get(i.file_id), i, bedSize, height)])),
    [state.items, dims, bedSize, height],
  );
  const labelOf = (i: PlateItem) => {
    const f = usableFiles.find((x) => x.id === i.file_id);
    return `${f ? baseName(f.rel_path) : i.file_id} #${i.copy_index + 1}`;
  };
  const offBed = state.items.filter((i) => fitsByUid.get(i.uid) === false);
  const selected = state.items.find((i) => i.uid === state.selected) ?? null;
  const canSave = ready && bedValid && state.items.length > 0 && offBed.length === 0 && !combine.isPending;

  function autoArrange() {
    if (!bedValid) return;
    const counts = new Map<number, number>();
    for (const i of state.items) counts.set(i.file_id, (counts.get(i.file_id) ?? 0) + 1);
    const req = autoRequest();
    req.items = usableFiles.filter((f) => counts.has(f.id)).map((f) => ({ file_id: f.id, copies: counts.get(f.id) ?? 1 }));
    if (req.items.length === 0) return;
    auto.mutate(req, {
      onSuccess: (res) => {
        setDims(new Map(res.parts.map((p) => [p.file_id, p])));
        // Keep each copy's scale/rotation-free pose from the server; reuse existing scales per file.
        const scaleOf = new Map(state.items.map((i) => [i.file_id, i.scale]));
        dispatch({
          type: "replace",
          items: res.poses.map((p) => ({
            uid: newUid(),
            file_id: p.file_id,
            x_mm: p.x_mm,
            y_mm: p.y_mm,
            rot_z_deg: 0,
            scale: scaleOf.get(p.file_id) ?? 1,
            copy_index: p.copy_index,
          })),
        });
        if (!res.fits) toast.warning(t("plate.autoNoFit"));
      },
      onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("plate.planFailed")),
    });
  }

  function save(thenSlice: boolean) {
    combine.mutate(buildManualRequest(state.items, { profileId: selectedProfile?.profile.id ?? null, bed: bedSize }, name), {
      onSuccess: (res) => {
        clearLayout(slug, fileIds);
        toast.success(t("plate.saved", { name: res.file ? baseName(res.file.rel_path) : "plate" }));
        if (thenSlice && res.file) setSliceFile(res.file);
        else onOpenChange(false);
      },
      onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("plate.saveFailed")),
    });
  }

  function onKeyDown(e: React.KeyboardEvent) {
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
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
    else if (k === "escape") dispatch({ type: "select", uid: null });
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
                  onSelect={(uid) => dispatch({ type: "select", uid })}
                  onBegin={() => dispatch({ type: "snapshot" })}
                  onChange={(uid, fields) => dispatch({ type: "patch", uid, fields, record: false })}
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
              <Button type="button" size="sm" variant="ghost" disabled={auto.isPending || !bedValid || !ready} onClick={autoArrange}>
                <WandSparklesIcon className="size-4" />
                {t("plate.autoArrange")}
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
            {usableFiles.map((f) => {
              const mine = state.items.filter((i) => i.file_id === f.id);
              return (
                <div key={f.id} className="space-y-1.5">
                  <div className="flex items-center gap-2">
                    <span className="size-2.5 shrink-0 rounded-sm" style={{ background: colorOf(f.id) }} />
                    <span className="min-w-0 flex-1 truncate font-mono text-xs" title={f.rel_path}>
                      {baseName(f.rel_path)}
                    </span>
                    <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.removeCopy", { name: baseName(f.rel_path) })} disabled={mine.length === 0} onClick={() => dispatch({ type: "removeLast", file_id: f.id })}>
                      <MinusIcon className="size-4" />
                    </Button>
                    <span className="w-6 text-center text-xs tabular-nums">{mine.length}</span>
                    <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.addCopy", { name: baseName(f.rel_path) })} disabled={state.items.length >= MAX_ITEMS} onClick={() => dispatch({ type: "addCopy", file_id: f.id })}>
                      <PlusIcon className="size-4" />
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
                          <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.duplicate")} disabled={state.items.length >= MAX_ITEMS} onClick={(e) => { e.stopPropagation(); dispatch({ type: "addCopy", file_id: i.file_id }); }}>
                            <CopyPlusIcon className="size-3.5" />
                          </Button>
                          <Button type="button" size="icon-sm" variant="ghost" aria-label={t("plate.deleteCopy")} onClick={(e) => { e.stopPropagation(); dispatch({ type: "removeCopy", uid: i.uid }); }}>
                            <Trash2Icon className="size-3.5" />
                          </Button>
                        </span>
                      </div>
                      <div className="grid grid-cols-4 gap-1">
                        <NumField label="X" value={i.x_mm} onFocus={() => dispatch({ type: "snapshot" })} onChange={(v) => dispatch({ type: "patch", uid: i.uid, fields: { x_mm: v }, record: false })} />
                        <NumField label="Y" value={i.y_mm} onFocus={() => dispatch({ type: "snapshot" })} onChange={(v) => dispatch({ type: "patch", uid: i.uid, fields: { y_mm: v }, record: false })} />
                        <NumField label={t("plate.rot")} value={i.rot_z_deg} onFocus={() => dispatch({ type: "snapshot" })} onChange={(v) => dispatch({ type: "patch", uid: i.uid, fields: { rot_z_deg: v }, record: false })} />
                        <NumField label="%" value={Math.round(i.scale * 10000) / 100} onFocus={() => dispatch({ type: "snapshot" })} onChange={(v) => dispatch({ type: "patch", uid: i.uid, fields: { scale: v / 100 }, record: false })} />
                      </div>
                    </div>
                  ))}
                </div>
              );
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
              className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
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
            <Input id="plate-name" placeholder="plate" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} className="h-8" />
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
