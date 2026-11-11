import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { DockviewApi } from "dockview-react";
import { useState } from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LAYOUT_KEY, flushLayout } from "./layoutStore";
import i18n from "i18next";
import { reloadLayoutLock } from "@/lib/layoutLock";
import { PANEL_TITLE_KEYS } from "./panelMeta";
import { LayoutMenu } from "@/components/dock/LayoutMenu";
import { addPanelAtDefault, buildDefaultLayout } from "./defaultLayout";
import { resetLayout } from "./layoutStore";
import { PANEL_IDS } from "./panelMeta";
import { getLayoutLocked } from "@/lib/layoutLock";
import { PrintDashProvider, type PrintDashValue } from "./PrintDashContext";
import { PrintDock } from "./PrintDock";

// Panel internals are covered elsewhere; each is one identifiable div here.
const { stub } = vi.hoisted(() => ({ stub: (testId: string) => () => <div data-testid={testId} /> }));
vi.mock("./PrintersPanel", () => ({ PrintersPanel: stub("p-printers") }));
vi.mock("./RailPanels", () => ({
  QueuePanel: stub("p-queue"),
  ArchivesPanel: stub("p-archives"),
  CameraPanel: stub("p-camera"),
  StatsPanel: stub("p-stats"),
  ForecastPanel: stub("p-forecast"),
  RailStack: stub("p-rail"),
}));

const value = { printers: [], groups: [], upNext: [], recentRuns: [], railExtras: [] } as unknown as PrintDashValue;

function Harness() {
  const [api, setApi] = useState<DockviewApi | null>(null);
  return (
    <PrintDashProvider value={value}>
      {api ? (
        <LayoutMenu
          panels={{ api, ids: PANEL_IDS, titleKeys: PANEL_TITLE_KEYS, add: addPanelAtDefault }}
          onReset={() => {
            resetLayout();
            buildDefaultLayout(api);
          }}
        />
      ) : null}
      <PrintDock onApi={setApi} />
    </PrintDashProvider>
  );
}

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, top: 0, left: 0, right: 1200, bottom: 800, width: 1200, height: 800, toJSON: () => ({}),
  });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, value: 1200 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 800 });
});

beforeEach(() => {
  localStorage.clear();
  reloadLayoutLock();
});

describe("PrintDock", () => {
  it("builds the default layout with a tab per panel", async () => {
    render(<Harness />);
    for (const title of Object.values(PANEL_TITLE_KEYS).map((k) => i18n.t(k))) expect(await screen.findByText(title)).toBeInTheDocument();
  });

  it("locks panels by default, yet the panel toggles still work while locked", async () => {
    const tabs = () => Array.from(document.querySelectorAll(".dv-tab")).map((el) => el.textContent);
    render(<Harness />);
    await screen.findByText(i18n.t(PANEL_TITLE_KEYS.queue));
    expect(document.querySelector(".dv-default-tab-action")).toBeNull();
    expect(getLayoutLocked()).toBe(true);
    fireEvent.pointerDown(await screen.findByRole("button", { name: "Layout" }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole("menuitemcheckbox", { name: i18n.t(PANEL_TITLE_KEYS.forecast) }));
    await waitFor(() => expect(tabs()).not.toContain(i18n.t(PANEL_TITLE_KEYS.forecast)));
  });

  it("Rearrange shows the banner; Esc and Done hide it", async () => {
    render(<Harness />);
    await screen.findByText(i18n.t(PANEL_TITLE_KEYS.queue));
    expect(screen.queryByTestId("layout-edit-banner")).toBeNull();
    const rearrange = async () => {
      fireEvent.pointerDown(await screen.findByRole("button", { name: "Layout" }), { button: 0, ctrlKey: false });
      fireEvent.click(await screen.findByRole("menuitem", { name: /Rearrange panels/ }));
    };
    await rearrange();
    expect(await screen.findByTestId("layout-edit-banner")).toHaveTextContent("Editing layout");
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("layout-edit-banner")).toBeNull());
    await rearrange();
    fireEvent.click(await screen.findByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByTestId("layout-edit-banner")).toBeNull());
    expect(getLayoutLocked()).toBe(true);
  });

  it("persists the layout, and Reset layout restores the default", async () => {
    const tabs = () => Array.from(document.querySelectorAll(".dv-tab")).map((el) => el.textContent);
    render(<Harness />);
    await waitFor(() => expect(tabs()).toContain(i18n.t(PANEL_TITLE_KEYS.forecast)));
    const openMenu = async () =>
      fireEvent.pointerDown(await screen.findByRole("button", { name: "Layout" }), { button: 0, ctrlKey: false });
    await openMenu();
    fireEvent.click(await screen.findByRole("menuitemcheckbox", { name: i18n.t(PANEL_TITLE_KEYS.forecast) }));
    await waitFor(() => expect(tabs()).not.toContain(i18n.t(PANEL_TITLE_KEYS.forecast)));
    flushLayout();
    const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY)!);
    expect(Object.keys(saved.layout.panels)).not.toContain("forecast");

    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await openMenu();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Reset layout" }));
    await waitFor(() => expect(tabs()).toContain(i18n.t(PANEL_TITLE_KEYS.forecast)));
  });

  it("falls back to the default layout when the saved one is corrupt", async () => {
    localStorage.setItem(LAYOUT_KEY, "{broken");
    render(<Harness />);
    expect(await screen.findByText(i18n.t(PANEL_TITLE_KEYS.queue))).toBeInTheDocument();
  });
});
