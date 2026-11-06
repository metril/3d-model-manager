import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PlateSelector } from "@/components/viewer/PlateSelector";
import type { PlateOut } from "@/api/types";

const plate = (index: number): PlateOut => ({
  index, prediction_s: null, weight_g: null, est_cost: null, thumbnail_available: false, filaments: [],
  name: `P${index}`, has_glb: true,
});

describe("PlateSelector", () => {
  it("renders nothing for fewer than two plates", () => {
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

describe("PlateSelector file label", () => {
  it("names the file even when only one file is plated", () => {
    render(
      <PlateSelector
        blobHash="h"
        plates={[plate(1), plate(2)]}
        value={null}
        onChange={() => {}}
        files={[{ fileId: 1, blobHash: "h", label: "0.2mm layer", plates: [plate(1), plate(2)] }]}
        onFileChange={() => {}}
      />,
    );
    expect(screen.getByText(/0\.2mm layer ·/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Plate 1\/2/ })).toBeInTheDocument();
  });
});
