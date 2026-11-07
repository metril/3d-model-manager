import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { PortalContainerContext } from "@/lib/portalContainer";
import { PlateSelector } from "@/components/viewer/PlateSelector";
import type { PlateOut } from "@/api/types";

beforeAll(() => {
  // Radix Select needs these in jsdom.
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.releasePointerCapture ??= () => {};
  Element.prototype.scrollIntoView ??= () => {};
});

const plate = (index: number): PlateOut => ({
  index, prediction_s: null, weight_g: null, est_cost: null, thumbnail_available: false, filaments: [],
  name: `P${index}`, has_glb: true,
});

describe("PlateSelector", () => {
  it("renders nothing for fewer than two plates and no profiles", () => {
    const { container, rerender } = render(<PlateSelector blobHash="h" plates={[plate(1)]} value={null} onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<PlateSelector blobHash="h" plates={null} value={null} onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists All plates + each plate and reports the choice", () => {
    const onChange = vi.fn();
    render(<PlateSelector blobHash="h" plates={[plate(1), plate(2)]} value={null} onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "All plates" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: /Plate 2\/2 · P2/ }));
    expect(onChange).toHaveBeenCalledWith(2);
  });
});

describe("PlateSelector print", () => {
  it("offers Print plate N only with a selected plate and calls onPrint", () => {
    const onPrint = vi.fn();
    const { rerender } = render(<PlateSelector blobHash="h" plates={[plate(1), plate(2)]} value={null} onChange={() => {}} onPrint={onPrint} />);
    expect(screen.queryByRole("button", { name: /Print plate/ })).not.toBeInTheDocument();
    rerender(<PlateSelector blobHash="h" plates={[plate(1), plate(2)]} value={2} onChange={() => {}} onPrint={onPrint} />);
    fireEvent.click(screen.getByRole("button", { name: "Print plate 2" }));
    expect(onPrint).toHaveBeenCalledWith(2);
  });

  it("offers Print plate for a single-plate file and calls onPrint(1)", () => {
    const onPrint = vi.fn();
    render(<PlateSelector blobHash="h" plates={[plate(1)]} value={null} onChange={() => {}} onPrint={onPrint} />);
    fireEvent.click(screen.getByRole("button", { name: "Print plate 1" }));
    expect(onPrint).toHaveBeenCalledWith(1);
  });

  it("hides Print plate N when no onPrint is given (no dialog mounted)", () => {
    render(<PlateSelector blobHash="h" plates={[plate(1), plate(2)]} value={2} onChange={() => {}} />);
    expect(screen.queryByRole("button", { name: /Print plate/ })).not.toBeInTheDocument();
  });
});

describe("PlateSelector profiles", () => {
  const profiles = [
    { fileId: 1, blobHash: "a", label: "100% infill", plates: [plate(1)] },
    { fileId: 2, blobHash: "b", label: "Canada dollar", plates: [plate(1)] },
    { fileId: 3, blobHash: "c", label: "No AMS", plates: [plate(1)] },
    { fileId: 4, blobHash: "d", label: "15% infill", plates: [plate(1), plate(2)] },
  ];
  const open = () => fireEvent.click(screen.getByRole("combobox", { name: /Print profile/ }));

  it("lists all profiles incl. single-plate ones, no All files without other meshes", () => {
    render(<PlateSelector blobHash="a" plates={profiles[0].plates} value={null} onChange={() => {}} files={profiles} onFileChange={() => {}} />);
    open();
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "100% infill · 1 plate", "Canada dollar · 1 plate", "No AMS · 1 plate", "15% infill · 2 plates",
    ]);
  });

  it("offers All files only with other meshes, and reports the pick", () => {
    const onFileChange = vi.fn();
    render(<PlateSelector blobHash="a" plates={profiles[0].plates} value={null} onChange={() => {}} files={profiles} hasAllFiles onFileChange={onFileChange} />);
    open();
    fireEvent.click(screen.getByRole("option", { name: "All files" }));
    expect(onFileChange).toHaveBeenCalledWith("*");
  });

  it("shows no chips for a 1-plate profile, chips for a 2-plate one", () => {
    const { rerender } = render(<PlateSelector blobHash="a" plates={profiles[0].plates} value={null} onChange={() => {}} files={profiles} onFileChange={() => {}} />);
    expect(screen.queryByRole("radio")).toBeNull();
    rerender(<PlateSelector blobHash="d" plates={profiles[3].plates} value={null} onChange={() => {}} files={profiles} onFileChange={() => {}} />);
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    expect(screen.getByRole("combobox")).toHaveTextContent("15% infill · 2 plates");
    expect(screen.getByRole("combobox").parentElement?.textContent).not.toMatch(/·\s*$/);
  });
});

describe("PlateSelector trigger + pause marker", () => {
  it("keeps the plate-count suffix as its own non-shrinking span and exposes the full label", () => {
    const files = [{ fileId: 1, blobHash: "a", label: "A very long profile title 15% infill no AM", plates: [plate(1), plate(2)] }];
    render(<PlateSelector blobHash="a" plates={files[0].plates} value={null} onChange={() => {}} files={files} onFileChange={() => {}} />);
    const trigger = screen.getByRole("combobox", { name: /Print profile/ });
    expect(trigger).toHaveAttribute("title", "A very long profile title 15% infill no AM · 2 plates");
    const suffix = screen.getByText("· 2 plates");
    expect(suffix).toHaveClass("shrink-0", "whitespace-nowrap");
    expect(screen.getByText("A very long profile title 15% infill no AM")).toHaveClass("truncate");
  });

  it("marks plates that pause", () => {
    render(<PlateSelector blobHash="h" plates={[{ ...plate(1), pauses: [{ z: 2 }] }, plate(2)]} value={null} onChange={() => {}} />);
    const chips = screen.getAllByRole("radio");
    expect(chips[1].querySelector("[data-testid=plate-pause]")).not.toBeNull();
    expect(chips[2].querySelector("[data-testid=plate-pause]")).toBeNull();
  });
});

describe("PlateSelector accessibility + portal + collapse", () => {
  const files = [
    { fileId: 1, blobHash: "a", label: "A", plates: [plate(1), plate(2)] },
    { fileId: 2, blobHash: "b", label: "B", plates: [plate(1)] },
  ];

  it("chips are a roving radiogroup: one tab stop, arrows move and select", () => {
    const onChange = vi.fn();
    render(<PlateSelector blobHash="h" plates={[plate(1), plate(2)]} value={1} onChange={onChange} />);
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => r.tabIndex)).toEqual([-1, 0, -1]);
    fireEvent.keyDown(radios[1], { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith(2);
    fireEvent.keyDown(radios[0], { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith(2);
    fireEvent.keyDown(radios[1], { key: "Home" });
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("gives screen readers text for too-large, colour-change and pause markers", () => {
    const p = { ...plate(1), fits: false, pauses: [{ z: 2 }], colour_changes: [{ z: 1, slot: 1, colour: "#00ff00" }] };
    render(<PlateSelector blobHash="h" plates={[p, plate(2)]} value={null} onChange={() => {}} />);
    const chip = screen.getByRole("radio", { name: /Plate 1\/2/ });
    expect(chip).toHaveTextContent("Too large");
    expect(chip).toHaveTextContent("1 colour change");
    expect(chip).toHaveTextContent("1 pause");
    expect(chip.querySelector(".sr-only")).not.toBeNull();
  });

  it("labels the profile trigger by a label + its value", () => {
    render(<PlateSelector blobHash="a" plates={files[0].plates} value={null} onChange={() => {}} files={files} onFileChange={() => {}} />);
    const trigger = screen.getByRole("combobox", { name: /Print profile/ });
    expect(trigger.getAttribute("aria-labelledby")?.split(" ")).toHaveLength(2);
  });

  it("opens the profile list inside the portal container (fullscreen-safe)", () => {
    const stage = document.createElement("div");
    document.body.appendChild(stage);
    render(
      <PortalContainerContext.Provider value={stage}>
        <PlateSelector blobHash="a" plates={files[0].plates} value={null} onChange={() => {}} files={files} onFileChange={() => {}} />
      </PortalContainerContext.Provider>,
    );
    fireEvent.click(screen.getByRole("combobox", { name: /Print profile/ }));
    expect(stage.querySelector("[role=listbox]")).not.toBeNull();
    stage.remove();
  });

  it("collapses to a compact select beyond six plates", () => {
    const many = [1, 2, 3, 4, 5, 6, 7].map((i) => plate(i));
    render(<PlateSelector blobHash="h" plates={many} value={null} onChange={() => {}} />);
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.getByRole("combobox", { name: "Plate" })).toBeInTheDocument();
  });

  it("lays chips out wrapped (no scroll wrapper swallowing pointer events)", () => {
    const { container } = render(<PlateSelector blobHash="h" plates={[plate(1), plate(2)]} value={null} onChange={() => {}} />);
    expect(container.firstElementChild).toHaveClass("flex-wrap");
    expect(container.firstElementChild).not.toHaveClass("overflow-x-auto");
  });
});
