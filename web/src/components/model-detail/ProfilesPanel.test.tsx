import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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
    // a single-plate profile shows no "1 plate" redundancy
    expect(screen.queryByText(/^1 plate$/)).toBeNull();
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
    expect(screen.getByTestId("colour-change")).toHaveTextContent("Colour change at 3.0 mm");
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
    fireEvent.click(within(screen.getByRole("group", { name: "b" })).getByRole("button"));
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

  it("keeps the swatch in the DOM, glued to the arrow in a nowrap span", () => {
    render(<ProfilesPanel model={model([file(1, "x.3mf", [plate(1, { colour_changes: [{ z: 2.2, slot: 1, colour: "#00ff00" }] })])])} />);
    const z = screen.getByTestId("colour-change-z");
    expect(z).toHaveClass("whitespace-nowrap");
    expect(z).toHaveTextContent("2.2 mm →");
    expect(z.querySelector("span[style]")).not.toBeNull();
    expect(screen.getByTestId("colour-change")).toHaveTextContent("Colour change at 2.2 mm →");
  });

  it("shows a pause note with an explanatory tooltip", () => {
    render(<ProfilesPanel model={model([file(1, "x.3mf", [plate(1, { pauses: [{ z: 2.2 }] })])])} />);
    const note = screen.getByTestId("pause-note");
    expect(note).toHaveTextContent("Pause at 2.2 mm");
    expect(note).toHaveAttribute("title", "Printer pauses here so you can swap filament manually");
    expect(note.querySelector("svg")).not.toBeNull();
  });
});

describe("ProfilesPanel not-ready profiles", () => {
  it("disables a card whose GLB is not ready and never selects it", () => {
    setActiveViewerBlob("h1");
    const pending = { ...file(2, "b.3mf", [plate(1)]), glb_status: "pending" } as unknown as FileOut;
    render(<ProfilesPanel model={model([file(1, "a.3mf", [plate(1)]), pending])} />);
    fireEvent.click(within(screen.getByRole("group", { name: "b" })).getByRole("button"));
    expect(screen.getByRole("group", { name: "b" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText(/3D preview pending/)).toBeInTheDocument();
    setActiveViewerBlob(null);
  });
});

describe("ProfilesPanel clicks, grouping, author", () => {
  it("the card background no longer selects (one handler, on the button)", () => {
    setActiveViewerBlob("h1");
    render(<ProfilesPanel model={model([file(1, "a.3mf", [plate(1), plate(2)]), file(2, "b.3mf", [plate(1)])])} />);
    fireEvent.click(screen.getByRole("group", { name: "b" }));
    expect(screen.getByRole("group", { name: "b" })).not.toHaveAttribute("aria-current");
    setActiveViewerBlob(null);
  });

  it("collapses duplicate files of the same blob into one card", () => {
    const dup = { ...file(2, "copy.3mf", [plate(1), plate(2)]), blob_hash: "h1" } as FileOut;
    render(<ProfilesPanel model={model([file(1, "a.3mf", [plate(1), plate(2)]), dup])} />);
    expect(screen.getAllByRole("group")).toHaveLength(1);
  });

  it("decodes an escaped author and shows N plates only from 2", () => {
    render(<ProfilesPanel model={model([file(1, "a.3mf", [plate(1), plate(2), plate(3)], { author: "Ann &amp; Bob" })])} />);
    expect(screen.getByText(/by Ann & Bob · 3 plates/)).toBeInTheDocument();
  });

  it("falls back to MakerWorld time/weight when a plate has none", () => {
    render(<ProfilesPanel model={model([file(1, "a.3mf", [plate(1), plate(2)], { plates: [{ index: 1, name: null, thumb: null, prediction: 3600, weight: 12 }] })])} />);
    expect(screen.getByText(/Plate 1 · 1h .*12 g/)).toBeInTheDocument();
  });

  it("returns to the model view when a sliced file is open and a profile is picked", () => {
    const sliced = { ...file(9, "x.gcode.3mf", null), kind: "sliced" } as FileOut;
    const onSelectAssembly = vi.fn();
    render(
      <ProfilesPanel
        model={model([file(1, "a.3mf", [plate(1), plate(2)]), sliced])}
        selection={{ type: "file", id: 9 }}
        onSelectAssembly={onSelectAssembly}
      />,
    );
    fireEvent.click(within(screen.getByRole("group", { name: "a" })).getAllByRole("button")[0]);
    expect(onSelectAssembly).toHaveBeenCalledTimes(1);
  });
});
