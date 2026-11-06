import { useParams, useSearch } from "@tanstack/react-router";

import { useModel } from "@/api/library";
import type { BackgroundPreset } from "@/components/viewer/background";
import type { LightingPreset } from "@/components/viewer/lighting";
import { decodePartColors } from "@/components/viewer/partColors";
import { ViewerStage } from "@/components/viewer/ViewerStage";
import { useViewerScene } from "@/components/viewer/useViewerScene";
import { glbFiles } from "@/components/viewer/viewable";
import { Skeleton } from "@/components/ui/skeleton";
import { type WindowSearch } from "@/pages/viewerWindowSearch";
import type { FileOut } from "@/api/types";
import type { SectionAxis, SectionState, ViewerToolsState } from "@/components/viewer/tools";


function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid h-svh w-svw place-items-center bg-background text-sm text-muted-foreground">
      {children}
    </div>
  );
}

function parseIds(ids: string | undefined): Set<number> {
  return new Set(
    (ids ?? "")
      .split(",")
      .filter((segment) => segment !== "") // Number("") is 0, not NaN -- drop empties first
      .map(Number)
      .filter((id) => Number.isInteger(id)),
  );
}

/** Decode `?bg=`/`?bgc=` into a background seed. Newer links carry the preset
 * name (`studio`, `custom`, …) so the window's Background control lands on the
 * right segment; `bgc` carries the custom hex. Older links (pre-preset) put a
 * resolved `#rrggbb` straight in `bg` -- treat that as a custom color so those
 * bookmarks still render exactly what they used to. An unknown preset string
 * degrades to the studio default inside `useViewerBackground`. */
function decodeBackground(
  bg: string | undefined,
  bgc: string | undefined,
): { preset: BackgroundPreset; custom?: string } {
  if (!bg) return { preset: "studio" };
  if (bg.startsWith("#")) return { preset: "custom", custom: bg };
  return { preset: bg as BackgroundPreset, custom: bgc };
}

const SECTION_AXES: readonly SectionAxis[] = ["x", "y", "z"];

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

/** Decode `?sec=<axis>:<t>` (e.g. `"x:0.35"`, see `openInWindow`) into a
 * `SectionState`. Anything that doesn't parse cleanly -- missing colon,
 * unknown axis, non-numeric `t` -- drops the WHOLE section rather than
 * guessing a default: a malformed link should render as "section off", not
 * silently enable one with a made-up axis/position. */
function decodeSection(sec: string | undefined): SectionState | undefined {
  if (!sec) return undefined;
  const [axis, tRaw] = sec.split(":");
  if (axis === undefined || tRaw === undefined) return undefined;
  if (!(SECTION_AXES as readonly string[]).includes(axis)) return undefined;
  const t = Number(tRaw);
  if (!Number.isFinite(t)) return undefined;
  return { enabled: true, axis: axis as SectionAxis, t: clamp01(t) };
}

/** Decode the Task 6 view-tools params into `useViewerScene`'s
 * `initial.tools` -- only the keys actually present in the URL are set, so
 * everything else falls through to `DEFAULT_TOOLS` inside `useViewerTools`.
 * `grid` is the one field `openInWindow` always writes (see its comment);
 * decoding it unconditionally when present just mirrors that -- a link
 * missing `grid` entirely (hand-typed, or from before Task 6) still falls
 * back to the hook's own default/localStorage-read behavior by leaving the
 * key absent here. */
function decodeTools(search: WindowSearch): Partial<ViewerToolsState> {
  const tools: Partial<ViewerToolsState> = {};
  if (search.grid !== undefined) tools.grid = search.grid !== "0";
  if (search.wf === "1") tools.shading = "wireframe";
  else if (search.xr === "1") tools.shading = "xray";
  if (search.rot === "1") tools.autoRotate = true;
  if (search.cam === "o") tools.ortho = true;

  const section = decodeSection(search.sec);
  if (section) tools.section = section;

  if (search.ex !== undefined) {
    const explode = Number(search.ex);
    if (Number.isFinite(explode)) tools.explode = clamp01(explode);
  }

  return tools;
}

/** The loaded half: the model's GLB parts are known, so it can call
 * `useViewerScene` unconditionally (hook rules forbid it after the early
 * returns above). Seeds the scene from the URL and renders the SAME stage as
 * the inline tab and Expand dialog -- full parts checklist, per-part colors,
 * Background, and Lighting -- so the pop-out is no longer a bare canvas. */
function ViewerWindow({ slug, files, search }: { slug: string; files: FileOut[]; search: WindowSearch }) {
  const requestedIds = parseIds(search.ids);
  const validRequested = files.filter((file) => requestedIds.has(file.id)).map((file) => file.id);
  // No ids -> show every part (the old "open the whole model" behavior). Ids
  // that no longer match any part (a stale link) fall back to the first part
  // rather than a blank window; the checklist lets the user pick from there.
  const checkedIds =
    requestedIds.size === 0
      ? files.map((file) => file.id)
      : validRequested.length > 0
        ? validRequested
        : [files[0].id];

  const { stageProps } = useViewerScene({
    slug,
    files,
    // The window is a pure URL-derived view: it reads its parts, colors,
    // background, and lighting from the URL and writes none of them back, so
    // popping out and comparing settings never mutates the tab's saved prefs.
    persist: false,
    initial: {
      checkedIds,
      colors: decodePartColors(search.colors),
      background: decodeBackground(search.bg, search.bgc),
      lighting: (search.light ?? "studio") as LightingPreset,
      tools: decodeTools(search),
      blob: search.blob,
      plate: Number.isFinite(Number(search.plate)) && search.plate ? Number(search.plate) : undefined,
    },
  });

  return (
    <div className="flex h-svh w-svw flex-col gap-2 bg-background p-2">
      <ViewerStage {...stageProps} variant="window" showWindowButtons={false} />
    </div>
  );
}

/** Standalone, chrome-less 3D viewer (M8 G1) rendered in its own browser
 * window via `window.open('/viewer/$slug?ids=&bg=&bgc=&light=&colors=')`.
 * Self-contained: it re-fetches the model and derives its parts + appearance
 * from the URL, so multiple windows (or one part per window) are independent.
 * Lives OUTSIDE the AppShell (no nav rail) but inside the session guard. */
export function ViewerWindowPage() {
  const { slug } = useParams({ strict: false });
  const search = useSearch({ strict: false }) as WindowSearch;
  const modelQuery = useModel(slug ?? "");

  if (modelQuery.isLoading) {
    return (
      <div className="h-svh w-svw bg-background p-2">
        <Skeleton className="h-full w-full rounded-lg" />
      </div>
    );
  }

  const model = modelQuery.data;
  if (!model) return <Centered>Model not found.</Centered>;

  const glbable = glbFiles(model);
  if (glbable.length === 0) return <Centered>No renderable parts selected.</Centered>;

  return <ViewerWindow slug={model.slug} files={glbable} search={search} />;
}
