import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ArchiveToolpath } from "@/components/print/ArchiveToolpath";

const { platesMock } = vi.hoisted(() => ({ platesMock: vi.fn() }));
vi.mock("@/api/archives", () => ({
  useArchiveGcodePlates: (id: number, enabled: boolean) => platesMock(id, enabled),
  archiveGcodeUrl: (id: number, plate?: number) => `/g/${id}?plate=${plate}`,
}));
vi.mock("@/components/gcode", () => ({
  LazyGcodeToolpathViewer: ({ gcodeUrl }: { gcodeUrl: string }) => <div data-testid="viewer">{gcodeUrl}</div>,
}));

describe("ArchiveToolpath", () => {
  beforeEach(() => platesMock.mockReset());

  it("lazy-mounts the viewer only after opening", () => {
    platesMock.mockReturnValue({ isPending: false, data: [{ plate: 1, bytes: 10 }] });
    render(<ArchiveToolpath archiveId={3} />);
    expect(screen.queryByTestId("viewer")).toBeNull();
    expect(platesMock).toHaveBeenLastCalledWith(3, false);
    fireEvent.click(screen.getByRole("button", { name: /show toolpath/i }));
    expect(screen.getByTestId("viewer").textContent).toBe("/g/3?plate=1");
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("shows a plate selector for multiple plates", () => {
    platesMock.mockReturnValue({
      isPending: false,
      data: [
        { plate: 1, bytes: 10 },
        { plate: 2, bytes: 20 },
      ],
    });
    render(<ArchiveToolpath archiveId={3} />);
    fireEvent.click(screen.getByRole("button", { name: /show toolpath/i }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "2" } });
    expect(screen.getByTestId("viewer").textContent).toBe("/g/3?plate=2");
  });

  it("shows the empty state when there are no plates / 404", () => {
    platesMock.mockReturnValue({ isPending: false, isError: true, data: undefined });
    render(<ArchiveToolpath archiveId={3} />);
    fireEvent.click(screen.getByRole("button", { name: /show toolpath/i }));
    expect(screen.getByText(/no g-code toolpath/i)).toBeTruthy();
    expect(screen.queryByTestId("viewer")).toBeNull();
  });
});
