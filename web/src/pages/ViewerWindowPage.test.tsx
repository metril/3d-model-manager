import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ViewerWindowPage } from "@/pages/ViewerWindowPage";
import type { FileOut, ModelDetail } from "@/api/types";

/** `useViewerScene` calls `useQueryClient()` unconditionally (R13a's Cover
 * action needs it to invalidate the model/gallery caches on capture) --
 * every render needs a `QueryClientProvider` ancestor now, even in the
 * pop-out window (`persist: false` just skips ever USING the client here). */
function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ViewerWindowPage />
    </QueryClientProvider>,
  );
}

type ViewerToolsStub = {
  grid: boolean;
  shading: string;
  autoRotate: boolean;
  ortho: boolean;
  section: { enabled: boolean; axis: string; t: number };
  explode: number;
};

const { paramsBox, searchBox, modelBox, modelViewerMock } = vi.hoisted(() => ({
  paramsBox: { current: { slug: "dragon" } as { slug?: string } },
  searchBox: {
    current: {} as {
      ids?: string;
      bg?: string;
      bgc?: string;
      light?: string;
      colors?: string;
      grid?: string;
      wf?: string;
      xr?: string;
      rot?: string;
      cam?: string;
      sec?: string;
      ex?: string;
    },
  },
  modelBox: { current: { data: undefined as unknown, isLoading: false } },
  modelViewerMock: vi.fn(
    ({
      parts,
      background,
      lighting,
      tools,
    }: {
      parts: { id: number; url: string; color?: string; visible: boolean }[];
      background: string;
      lighting?: { contactShadow: boolean };
      tools?: ViewerToolsStub;
    }) => (
      <div
        data-testid="model-viewer"
        data-background={background}
        data-contact-shadow={lighting ? String(lighting.contactShadow) : undefined}
        data-grid={tools ? String(tools.grid) : undefined}
        data-shading={tools ? tools.shading : undefined}
        data-auto-rotate={tools ? String(tools.autoRotate) : undefined}
        data-ortho={tools ? String(tools.ortho) : undefined}
        data-section={
          tools ? `${tools.section.enabled}:${tools.section.axis}:${tools.section.t}` : undefined
        }
        data-explode={tools ? String(tools.explode) : undefined}
      >
        {parts.map((part) => `${part.id}:${part.color ?? "none"}`).join(",")}
      </div>
    ),
  ),
}));

vi.mock("@tanstack/react-router", () => ({
  useParams: () => paramsBox.current,
  useSearch: () => searchBox.current,
}));
vi.mock("@/api/library", () => ({
  useModel: () => modelBox.current,
  modelQueryOptions: (slug: string) => ({ queryKey: ["models", "detail", slug] }),
  uploadModelCover: vi.fn(),
}));
vi.mock("@/components/viewer/ModelViewer", () => ({ default: modelViewerMock }));
// The window now renders the full ViewerStage, which reaches for the printer
// (AMS color sync) and the app theme (the "Match theme" background). Neither
// is under test here -- stub both to a quiet default so the stage mounts.
vi.mock("@/api/printers", () => ({
  usePrinters: () => ({ data: [] }),
  usePrinterStatus: () => ({ data: undefined }),
}));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }));
// R13a re-chrome: Parts (`ViewerTopOverlay`) and Background/Lighting/Section/
// etc. (`ViewerMorePanel`) now live inside Popovers, which never reach an
// interactive open state under jsdom (same convention as
// `LibraryPage.test.tsx`) -- render trigger/content unconditionally so this
// file's existing assertions (checkboxes, radiogroups) stay reachable
// without a real open click.
vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverTrigger: ({ children }: { children?: ReactNode }) => <>{children}</>,
  PopoverContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));

function glbFile(id: number, hash: string, rel: string): FileOut {
  return {
    id,
    revision_id: 1,
    rel_path: rel,
    storage_path: "",
    blob_hash: hash,
    size: 1,
    format: "glb",
    kind: "mesh",
    mtime: null,
    verified_at: null,
    meta: null,
    thumb_ready: true,
    glb_status: "ok",
    glb_preview_ready: false,
  } as unknown as FileOut;
}

function fakeModel(files: FileOut[]): ModelDetail {
  return { current_revision: { files } } as unknown as ModelDetail;
}

beforeEach(() => {
  modelViewerMock.mockClear();
  localStorage.clear();
  paramsBox.current = { slug: "dragon" };
  searchBox.current = {};
  modelBox.current = { data: undefined, isLoading: false };
});

describe("ViewerWindowPage", () => {
  it("renders only the requested ids, with their colors and background from the URL", async () => {
    modelBox.current = {
      data: fakeModel([glbFile(1, "aaa", "a.glb"), glbFile(2, "bbb", "b.glb")]),
      isLoading: false,
    };
    searchBox.current = { ids: "2", bg: "#112233", colors: "2:ff0000" };

    renderPage();

    const viewer = await screen.findByTestId("model-viewer");
    expect(viewer).toHaveAttribute("data-background", "#112233");
    // B1 "toggle-fix core": every combinable part is always in `parts` --
    // only id 2 (the requested one) is `visible`; colors still flow to the
    // matching part regardless.
    const parts = modelViewerMock.mock.calls.at(-1)?.[0].parts;
    expect(parts).toEqual([
      { id: 1, url: "/api/blobs/aaa/glb", color: undefined, visible: false },
      { id: 2, url: "/api/blobs/bbb/glb", color: "#ff0000", visible: true },
    ]);
  });

  it("renders every GLB part when no ids are given, with the default background", async () => {
    modelBox.current = {
      data: fakeModel([glbFile(1, "aaa", "a.glb"), glbFile(2, "bbb", "b.glb")]),
      isLoading: false,
    };

    renderPage();

    const viewer = await screen.findByTestId("model-viewer");
    expect(viewer).toHaveAttribute("data-background", "#9aa5b8");
    const parts = modelViewerMock.mock.calls.at(-1)?.[0].parts;
    expect((parts ?? []).map((part: { id: number }) => part.id)).toEqual([1, 2]);
  });

  it("shows a not-found message when the model is missing", async () => {
    modelBox.current = { data: undefined, isLoading: false };
    renderPage();
    expect(await screen.findByText("Model not found.")).toBeInTheDocument();
    expect(screen.queryByTestId("model-viewer")).not.toBeInTheDocument();
  });

  it("renders the full stage -- parts checklist and appearance controls -- not a bare canvas", async () => {
    modelBox.current = {
      data: fakeModel([glbFile(1, "aaa", "a.glb"), glbFile(2, "bbb", "b.glb")]),
      isLoading: false,
    };
    searchBox.current = { ids: "1" };

    renderPage();

    await screen.findByTestId("model-viewer");
    expect(screen.getByRole("radiogroup", { name: "Background" })).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Lighting" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "a.glb" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "b.glb" })).not.toBeChecked();
  });

  it("checking another part in the window marks it visible in the rendered parts", async () => {
    // B1 "toggle-fix core": both parts are always in `parts` (checking id 1
    // via `ids=1` doesn't drop id 2) -- checking the second part's checkbox
    // only flips its `visible` flag.
    modelBox.current = {
      data: fakeModel([glbFile(1, "aaa", "a.glb"), glbFile(2, "bbb", "b.glb")]),
      isLoading: false,
    };
    searchBox.current = { ids: "1" };

    renderPage();
    await screen.findByTestId("model-viewer");

    const before = modelViewerMock.mock.calls.at(-1)?.[0].parts;
    expect((before ?? []).map((part: { id: number; visible: boolean }) => [part.id, part.visible])).toEqual([
      [1, true],
      [2, false],
    ]);

    fireEvent.click(screen.getByRole("checkbox", { name: "b.glb" }));

    await waitFor(() => {
      const parts = modelViewerMock.mock.calls.at(-1)?.[0].parts;
      expect((parts ?? []).map((part: { id: number; visible: boolean }) => [part.id, part.visible])).toEqual([
        [1, true],
        [2, true],
      ]);
    });
  });

  it("All/None in the window variant check and uncheck every part", async () => {
    modelBox.current = {
      data: fakeModel([glbFile(1, "aaa", "a.glb"), glbFile(2, "bbb", "b.glb")]),
      isLoading: false,
    };
    searchBox.current = { ids: "1" };

    renderPage();
    await screen.findByTestId("model-viewer");

    expect(screen.getByRole("checkbox", { name: "a.glb" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "b.glb" })).not.toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "Show all parts" }));
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: "a.glb" })).toBeChecked();
      expect(screen.getByRole("checkbox", { name: "b.glb" })).toBeChecked();
    });

    fireEvent.click(screen.getByRole("button", { name: "None — hide all parts" }));
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: "a.glb" })).not.toBeChecked();
      expect(screen.getByRole("checkbox", { name: "b.glb" })).not.toBeChecked();
    });
  });

  it("resolves the white background preset from the URL", async () => {
    modelBox.current = { data: fakeModel([glbFile(1, "aaa", "a.glb")]), isLoading: false };
    searchBox.current = { bg: "white" };

    renderPage();

    expect(await screen.findByTestId("model-viewer")).toHaveAttribute("data-background", "#ffffff");
  });

  it("seeds tools.grid/shading/section from ?wf=1&grid=0&sec=y:0.25", async () => {
    modelBox.current = { data: fakeModel([glbFile(1, "aaa", "a.glb")]), isLoading: false };
    searchBox.current = { wf: "1", grid: "0", sec: "y:0.25" };

    renderPage();

    const viewer = await screen.findByTestId("model-viewer");
    expect(viewer).toHaveAttribute("data-grid", "false");
    expect(viewer).toHaveAttribute("data-shading", "wireframe");
    expect(viewer).toHaveAttribute("data-section", "true:y:0.25");
    // Unset tools params keep their defaults.
    expect(viewer).toHaveAttribute("data-auto-rotate", "false");
    expect(viewer).toHaveAttribute("data-ortho", "false");
    expect(viewer).toHaveAttribute("data-explode", "0");
  });

  it("seeds tools.shading = xray from ?xr=1", async () => {
    modelBox.current = { data: fakeModel([glbFile(1, "aaa", "a.glb")]), isLoading: false };
    searchBox.current = { xr: "1" };

    renderPage();

    expect(await screen.findByTestId("model-viewer")).toHaveAttribute("data-shading", "xray");
  });

  it("ignores a malformed sec param instead of guessing a default", async () => {
    modelBox.current = { data: fakeModel([glbFile(1, "aaa", "a.glb")]), isLoading: false };
    searchBox.current = { sec: "diagonal:0.5" };

    renderPage();

    expect(await screen.findByTestId("model-viewer")).toHaveAttribute("data-section", "false:x:0.5");
  });

  it("clamps ex to [0,1]", async () => {
    modelBox.current = { data: fakeModel([glbFile(1, "aaa", "a.glb")]), isLoading: false };
    searchBox.current = { ex: "2.5" };

    renderPage();

    expect(await screen.findByTestId("model-viewer")).toHaveAttribute("data-explode", "1");
  });

  it("does not persist appearance changes -- the window is a URL-derived view, not the tab's prefs", async () => {
    modelBox.current = { data: fakeModel([glbFile(1, "aaa", "a.glb")]), isLoading: false };
    // No light param -> seeds studio (shadow on).
    renderPage();
    await screen.findByTestId("model-viewer");

    // "Flat" is unique to the Lighting group. Switching it proves the change
    // took effect in-window...
    fireEvent.click(screen.getByRole("radio", { name: "Flat" }));
    await waitFor(() =>
      expect(screen.getByTestId("model-viewer")).toHaveAttribute("data-contact-shadow", "false"),
    );

    // ...but nothing leaked into the tab's shared localStorage prefs.
    expect(localStorage.getItem("viewer-lighting")).toBeNull();
    expect(localStorage.getItem("viewer-bg")).toBeNull();
  });

  it("does not persist tool changes -- toggling grid in the window never writes viewer-tools", async () => {
    modelBox.current = { data: fakeModel([glbFile(1, "aaa", "a.glb")]), isLoading: false };
    renderPage();
    await screen.findByTestId("model-viewer");

    fireEvent.click(screen.getByRole("button", { name: "Grid" }));

    await waitFor(() => expect(screen.getByTestId("model-viewer")).toHaveAttribute("data-grid", "false"));
    expect(localStorage.getItem("viewer-tools")).toBeNull();
  });

  it("the flat lighting preset from the URL turns the contact shadow off; studio (default) keeps it on", async () => {
    modelBox.current = { data: fakeModel([glbFile(1, "aaa", "a.glb")]), isLoading: false };
    searchBox.current = { light: "flat" };

    const { unmount } = renderPage();
    expect(await screen.findByTestId("model-viewer")).toHaveAttribute("data-contact-shadow", "false");
    unmount();

    searchBox.current = {};
    renderPage();
    expect(await screen.findByTestId("model-viewer")).toHaveAttribute("data-contact-shadow", "true");
  });
});
