import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProfilesPanel } from "@/components/model-detail/ProfilesPanel";
import { getSelectedPlate, setActiveViewerBlob, setSelectedPlate } from "@/components/viewer/plates";
import type { FileOut, ModelDetail, PlateOut } from "@/api/types";

const plate = (index: number, extra: Partial<PlateOut> = {}): PlateOut => ({
  index, prediction_s: null, weight_g: null, est_cost: null, thumbnail_available: false,
  filaments: [{ type: "PLA", color: "#ff0000", used_m: null, used_g: null, slot: 0 }], has_glb: true, ...extra,
});
const file = (id: number, rel_path: string, plates: PlateOut[] | null, source_meta?: FileOut["source_meta"]): FileOut =>
  ({ id, rel_path, blob_hash: `h${id}`, format: "3mf", kind: "model", glb_status: "ok", meta: { plates } as FileOut["meta"], source_meta }) as unknown as FileOut;
const model = (files: FileOut[]) => ({ current_revision: { files } }) as unknown as ModelDetail;

describe("ProfilesPanel", () => {
  it("uses source_meta title, else the stripped file label; shows plate counts", () => {
    render(
      <ProfilesPanel
        model={model([
          file(1, "a/0.2mm layer-2007919.3mf", [plate(1), plate(2)], { title: "Fancy Profile", author: "Ann", description: "Long desc" }),
          file(2, "a/0.4mm draft-2007920.3mf", [plate(1, { has_glb: false })]),
        ])}
      />,
    );
    expect(screen.getByText("Fancy Profile")).toBeInTheDocument();
    expect(screen.getByText("0.4mm draft")).toBeInTheDocument();
    expect(screen.getByText(/by Ann · 2 plates/)).toBeInTheDocument();
    expect(screen.getByText(/^1 plate$/)).toBeInTheDocument();
    expect(screen.getByTitle("Long desc")).toBeInTheDocument();
    // a select button per card + the two selectable plates of the 2-plate profile
    expect(screen.getAllByRole("button")).toHaveLength(4);
  });

  it("renders escaped html description as plain text", () => {
    render(<ProfilesPanel model={model([file(1, "x.3mf", [plate(1)], { description: "&lt;p&gt;no ams.&lt;/p&gt;" })])} />);
    expect(screen.getByTitle("no ams.")).toHaveTextContent("no ams.");
  });

  it("shows the colour-change note", () => {
    render(<ProfilesPanel model={model([file(1, "x.3mf", [plate(1, { colour_changes: [{ z: 3, slot: 1, colour: "#00ff00" }] })])])} />);
    expect(screen.getByText(/Colour change at 3\.0 mm/)).toBeInTheDocument();
  });

  it("renders nothing for a lone 3MF without plates", () => {
    const { container } = render(<ProfilesPanel model={model([file(1, "x.3mf", null)])} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for several 3MFs none of which have plates", () => {
    const { container } = render(<ProfilesPanel model={model([file(1, "a.3mf", null), file(2, "b.3mf", [])])} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ProfilesPanel selection + colour note", () => {
  it("clicking a single-plate card selects that profile in the shared viewer state", () => {
    setActiveViewerBlob("h1");
    render(<ProfilesPanel model={model([file(1, "a.3mf", [plate(1), plate(2)]), file(2, "b.3mf", [plate(1, { has_glb: false })])])} />);
    expect(screen.getByRole("group", { name: "a" })).toHaveAttribute("aria-current", "true");
    fireEvent.click(screen.getByRole("group", { name: "b" }));
    expect(screen.getByRole("group", { name: "b" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("group", { name: "a" })).not.toHaveAttribute("aria-current");
    setActiveViewerBlob(null);
  });

  it("a plate tile selects its profile and plate", () => {
    setActiveViewerBlob("h2");
    render(<ProfilesPanel model={model([file(1, "a.3mf", [plate(1), plate(2)]), file(2, "b.3mf", [plate(1)])])} />);
    fireEvent.click(screen.getAllByTitle("Show this plate in the viewer")[1]);
    expect(getSelectedPlate("h1")).toBe(2);
    expect(screen.getByRole("group", { name: "a" })).toHaveAttribute("aria-current", "true");
    setActiveViewerBlob(null);
    setSelectedPlate("h1", null);
  });

  it("keeps the colour swatch inside the same nowrap element as the text", () => {
    render(<ProfilesPanel model={model([file(1, "x.3mf", [plate(1, { colour_changes: [{ z: 2.2, slot: 1, colour: "#00ff00" }] })])])} />);
    const note = screen.getByTestId("colour-change");
    expect(note).toHaveClass("whitespace-nowrap");
    expect(note).toHaveTextContent("Colour change at 2.2 mm →");
    expect(note.querySelector("span[style]")).not.toBeNull();
  });
});

describe("ProfilesPanel not-ready profiles", () => {
  it("disables a card whose GLB is not ready and never selects it", () => {
    setActiveViewerBlob("h1");
    const pending = { ...file(2, "b.3mf", [plate(1)]), glb_status: "pending" } as unknown as FileOut;
    render(<ProfilesPanel model={model([file(1, "a.3mf", [plate(1)]), pending])} />);
    fireEvent.click(screen.getByRole("group", { name: "b" }));
    expect(screen.getByRole("group", { name: "b" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText(/3D preview pending/)).toBeInTheDocument();
    setActiveViewerBlob(null);
  });
});
