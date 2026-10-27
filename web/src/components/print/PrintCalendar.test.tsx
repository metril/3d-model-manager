import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PrintCalendar } from "@/components/print/PrintCalendar";

const { calMock } = vi.hoisted(() => ({ calMock: vi.fn() }));
vi.mock("@/api/archives", () => ({
  useArchiveCalendar: (from: string, to: string) => ({ data: calMock(from, to), isLoading: false }),
}));

const now = new Date();
const ym = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

describe("PrintCalendar", () => {
  it("renders counts/hours per day and selects a day on click", () => {
    calMock.mockReturnValue([
      { date: `${ym}-05`, count: 3, ok: 3, failed: 0, hours: 4.5 },
      { date: `${ym}-06`, count: 2, ok: 0, failed: 2, hours: 1 },
    ]);
    const onSelect = vi.fn();
    render(<PrintCalendar onSelectDay={onSelect} />);
    const d5 = screen.getByTestId(`day-${ym}-05`);
    expect(d5).toHaveTextContent("3 · 4.5h");
    expect(d5.className).toContain("bg-success");
    expect(screen.getByTestId(`day-${ym}-06`).className).toContain("bg-destructive");
    expect(screen.getByTestId(`day-${ym}-07`)).toBeDisabled();
    fireEvent.click(d5);
    expect(onSelect).toHaveBeenCalledWith(`${ym}-05`);
  });

  it("requests the previous month on prev", () => {
    calMock.mockReturnValue([]);
    render(<PrintCalendar onSelectDay={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Previous month" }));
    const prev = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    const last = calMock.mock.calls.at(-1)!;
    expect(last[0]).toBe(
      `${prev.getFullYear()}-${String(prev.getMonth() + 1).padStart(2, "0")}-01`,
    );
  });
});
