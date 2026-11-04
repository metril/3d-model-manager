import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PlatePicker } from "@/components/print/PlatePicker";
import type { PlateOut } from "@/api/types";

vi.mock("@/components/print/PlatePreview", () => ({
  default: ({ plate }: { plate: number }) => <div data-testid="plate-preview">preview {plate}</div>,
}));

const plate = (index: number, over: Partial<PlateOut> = {}): PlateOut => ({
  index, prediction_s: null, weight_g: null, est_cost: null, thumbnail_available: true, filaments: [],
  name: `P${index}`, has_glb: true, fits: true, ...over,
});

describe("PlatePicker", () => {
  it("maps All plates to null, badges non-fitting plates and previews the selection", async () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <PlatePicker blobHash="h" plates={[plate(1), plate(2, { fits: false })]} value={null} onChange={onChange} allowAll />,
    );
    expect(screen.getAllByText("Too large")).toHaveLength(1);
    expect(screen.getByText(/only plate 1 prints/)).toBeInTheDocument();
    expect(screen.queryByTestId("plate-preview")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /Plate 2/ }));
    expect(onChange).toHaveBeenCalledWith(2);
    fireEvent.click(screen.getByRole("radio", { name: /All plates/ }));
    expect(onChange).toHaveBeenCalledWith(null);

    rerender(<PlatePicker blobHash="h" plates={[plate(1), plate(2)]} value={1} onChange={onChange} allowAll />);
    expect(await screen.findByTestId("plate-preview")).toHaveTextContent("preview 1");
    expect(screen.queryByText(/only plate 1 prints/)).not.toBeInTheDocument();
  });

  it("omits All plates and the preview when not asked", () => {
    render(<PlatePicker blobHash="h" plates={[plate(1), plate(2)]} value={1} onChange={() => {}} preview={false} />);
    expect(screen.queryByRole("radio", { name: /All plates/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("plate-preview")).not.toBeInTheDocument();
  });
});
