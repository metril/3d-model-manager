import { render, screen } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelDetail } from "@/api/types";
import { ModelDetailProvider, type ModelDetailContextValue } from "./ModelDetailContext";
import { ModelDetailDock } from "./ModelDetailDock";
import { StackedPanels } from "./StackedPanels";
import { LAYOUT_KEY } from "./layoutStore";
import { PANEL_TITLES } from "./panelMeta";

// Card internals are covered elsewhere; here each is a single identifiable div.
const { stub } = vi.hoisted(() => ({ stub: (testId: string) => () => <div data-testid={testId} /> }));
vi.mock("@/components/model-detail/StudioWorkspace", () => ({ StudioWorkspace: stub("card-studio") }));
vi.mock("@/components/model-detail/RelatedModels", () => ({ RelatedModels: stub("card-related") }));
vi.mock("@/components/model-detail/cards/DescriptionCard", () => ({ DescriptionCard: stub("card-description") }));
vi.mock("@/components/model-detail/cards/TagsLinksCard", () => ({ TagsLinksCard: stub("card-tags-links") }));
vi.mock("@/components/model-detail/cards/GcodeProfilesCard", () => ({ GcodeProfilesCard: stub("card-gcode") }));
vi.mock("@/components/model-detail/cards/PrintHistoryCard", () => ({ PrintHistoryCard: stub("card-history") }));
vi.mock("@/components/model-detail/cards/FilesDocsCard", () => ({ FilesDocsCard: stub("card-files") }));
vi.mock("@/components/model-detail/cards/RevisionsCard", () => ({ RevisionsCard: stub("card-revisions") }));
vi.mock("@/components/model-detail/cards/NotesCard", () => ({ NotesCard: stub("card-notes") }));
vi.mock("@/components/model-detail/cards/PrintTipsCard", () => ({ PrintTipsCard: stub("card-tips") }));
vi.mock("@/components/model-detail/cards/SpecsCard", () => ({ SpecsCard: stub("card-specs") }));

const value = {
  model: { id: 1, slug: "m", name: "M" } as ModelDetail,
  editMode: false,
  setEditMode: vi.fn(),
  relocateOpen: false,
  setRelocateOpen: vi.fn(),
  studio: { selection: undefined, glbable: [], others: [], onSelectAssembly: vi.fn(), onViewIn3D: vi.fn() },
} as ModelDetailContextValue;

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  // dockview sizes itself from layout metrics jsdom doesn't compute.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, top: 0, left: 0, right: 1200, bottom: 800, width: 1200, height: 800, toJSON: () => ({}),
  });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, value: 1200 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 800 });
});

beforeEach(() => localStorage.clear());

describe("ModelDetailDock", () => {
  it("renders a tab for every registry panel in the default layout", async () => {
    render(
      <ModelDetailProvider value={value}>
        <ModelDetailDock />
      </ModelDetailProvider>,
    );
    for (const title of Object.values(PANEL_TITLES)) {
      expect(await screen.findByText(title)).toBeInTheDocument();
    }
  });

  it("falls back to the default layout when the saved one is corrupt", async () => {
    localStorage.setItem(LAYOUT_KEY, "{broken");
    render(
      <ModelDetailProvider value={value}>
        <ModelDetailDock />
      </ModelDetailProvider>,
    );
    expect(await screen.findByText(PANEL_TITLES.notes)).toBeInTheDocument();
  });
});

describe("StackedPanels (<900px fallback)", () => {
  it("renders every panel body stacked, left column first, with no dock", () => {
    render(
      <ModelDetailProvider value={value}>
        <StackedPanels />
      </ModelDetailProvider>,
    );
    expect(screen.queryByTestId("model-detail-dock")).toBeNull();
    const ids = Array.from(document.querySelectorAll("[data-testid^='card-']")).map((el) => el.getAttribute("data-testid"));
    expect(ids).toEqual([
      "card-studio", "card-description", "card-tags-links", "card-related",
      "card-gcode", "card-history", "card-files", "card-revisions", "card-notes", "card-tips", "card-specs",
    ]);
  });
});
