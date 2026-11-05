import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProfilesPanel } from "@/components/model-detail/ProfilesPanel";
import type { FileOut, ModelDetail, PlateOut } from "@/api/types";

const plate = (index: number, extra: Partial<PlateOut> = {}): PlateOut => ({
  index, prediction_s: null, weight_g: null, est_cost: null, thumbnail_available: false,
  filaments: [{ type: "PLA", color: "#ff0000", used_m: null, used_g: null, slot: 0 }], has_glb: true, ...extra,
});
const file = (id: number, rel_path: string, plates: PlateOut[] | null, source_meta?: FileOut["source_meta"]): FileOut =>
  ({ id, rel_path, blob_hash: `h${id}`, format: "3mf", kind: "model", meta: { plates } as FileOut["meta"], source_meta }) as unknown as FileOut;
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
    // only the GLB plates are interactive
    expect(screen.getAllByRole("button")).toHaveLength(2);
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
