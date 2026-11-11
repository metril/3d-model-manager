import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { DockviewApi } from "dockview-react";
import { useState } from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LAYOUT_KEY, flushLayout } from "./layoutStore";
import i18n from "i18next";
import { reloadLayoutLock, setLayoutLocked } from "@/lib/layoutLock";
import { PANEL_TITLE_KEYS } from "./panelMeta";
import { PanelsMenu } from "./PanelsMenu";
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
      {api ? <PanelsMenu api={api} /> : null}
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

  it("locks panels by default: no close buttons, panel toggles disabled", async () => {
    render(<Harness />);
    await screen.findByText(i18n.t(PANEL_TITLE_KEYS.queue));
    expect(document.querySelector(".dv-default-tab-action")).toBeNull();
    fireEvent.pointerDown(await screen.findByRole("button", { name: "Panels" }), { button: 0, ctrlKey: false });
    expect(await screen.findByRole("menuitemcheckbox", { name: i18n.t(PANEL_TITLE_KEYS.forecast) })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Unlock layout to change panels")).toBeInTheDocument();
  });

  it("persists the layout, and Reset layout restores the default", async () => {
    setLayoutLocked(false);
    const tabs = () => Array.from(document.querySelectorAll(".dv-tab")).map((el) => el.textContent);
    render(<Harness />);
    await waitFor(() => expect(tabs()).toContain(i18n.t(PANEL_TITLE_KEYS.forecast)));
    const openMenu = async () =>
      fireEvent.pointerDown(await screen.findByRole("button", { name: "Panels" }), { button: 0, ctrlKey: false });
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
