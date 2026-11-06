import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PauseWarning } from "@/components/print/PauseWarning";
import type { PlateOut } from "@/api/types";

const plate = (index: number, pauses?: { z: number }[]): PlateOut => ({
  index, prediction_s: null, weight_g: null, est_cost: null, thumbnail_available: false, filaments: [], pauses,
});

describe("PauseWarning", () => {
  it("warns for the chosen plate with the pause height", () => {
    render(<PauseWarning plates={[plate(1), plate(2, [{ z: 2.2 }])]} plate={2} />);
    expect(screen.getByTestId("pause-warning")).toHaveTextContent("This plate pauses at 2.2 mm for a manual filament swap.");
  });
  it("stays quiet when the chosen plate has no pause", () => {
    render(<PauseWarning plates={[plate(1), plate(2, [{ z: 2.2 }])]} plate={1} />);
    expect(screen.queryByTestId("pause-warning")).toBeNull();
  });
  it("for All plates, counts plates that pause", () => {
    render(<PauseWarning plates={[plate(1, [{ z: 1 }]), plate(2, [{ z: 2 }]), plate(3)]} plate={null} />);
    expect(screen.getByTestId("pause-warning")).toHaveTextContent("2 plates pause for a manual filament swap.");
  });
});
