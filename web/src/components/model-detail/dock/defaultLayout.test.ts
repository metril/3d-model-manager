import type { DockviewApi } from "dockview-react";
import { describe, expect, it } from "vitest";

import { addPanelAtDefault, buildDefaultLayout } from "./defaultLayout";
import { PANEL_IDS, type PanelId } from "./panelMeta";

type Call = { id: string; position?: { referencePanel: { id: string }; direction: string }; renderer?: string };

function fakeApi(initial: PanelId[] = []) {
  const panels = new Map<string, { id: string; api: { setActive: () => void; setSize: () => void } }>();
  const calls: Call[] = [];
  const api = {
    width: 0,
    height: 0,
    clear: () => panels.clear(),
    getPanel: (id: string) => panels.get(id),
    addPanel: (options: Call) => {
      calls.push(options);
      const panel = { id: options.id, api: { setActive: () => {}, setSize: () => {} } };
      panels.set(options.id, panel);
      return panel;
    },
  };
  initial.forEach((id) => api.addPanel({ id }));
  calls.length = 0;
  return { api: api as unknown as DockviewApi, calls };
}

describe("defaultLayout", () => {
  it("builds all eleven panels, viewer always-rendered", () => {
    const { api, calls } = fakeApi();
    buildDefaultLayout(api);
    expect(calls.map((c) => c.id).sort()).toEqual([...PANEL_IDS].sort());
    expect(calls.find((c) => c.id === "viewer")?.renderer).toBe("always");
    expect(calls.find((c) => c.id === "related")?.position).toMatchObject({
      referencePanel: { id: "description" },
      direction: "within",
    });
    expect(calls.find((c) => c.id === "gcodeProfiles")?.position).toMatchObject({ direction: "right" });
    expect(calls.find((c) => c.id === "revisions")?.position).toMatchObject({
      referencePanel: { id: "gcodeProfiles" },
      direction: "below",
    });
  });

  it("re-adds a closed right card as a tab of its default group", () => {
    const { api, calls } = fakeApi(["viewer", "revisions", "printTips", "filesDocs"]);
    addPanelAtDefault(api, "notes");
    expect(calls[0].position).toMatchObject({ referencePanel: { id: "revisions" }, direction: "within" });
  });

  it("re-adds a whole closed group next to its neighbour group", () => {
    const { api, calls } = fakeApi(["viewer", "filesDocs"]);
    addPanelAtDefault(api, "specs");
    expect(calls[0].position).toMatchObject({ referencePanel: { id: "filesDocs" }, direction: "below" });
  });

  it("re-adds a left tab into the existing tab group, else below the viewer", () => {
    const withSibling = fakeApi(["viewer", "description"]);
    addPanelAtDefault(withSibling.api, "related");
    expect(withSibling.calls[0].position).toMatchObject({ referencePanel: { id: "description" }, direction: "within" });
    const alone = fakeApi(["viewer"]);
    addPanelAtDefault(alone.api, "description");
    expect(alone.calls[0].position).toMatchObject({ referencePanel: { id: "viewer" }, direction: "below" });
  });
});
