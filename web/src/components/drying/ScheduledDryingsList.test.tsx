import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ScheduledDrying } from "@/api/types";
import { ScheduledDryingsList } from "./ScheduledDryingsList";
import { a1Mini } from "../print/card/testUtils";

const { getMock, deleteMock } = vi.hoisted(() => ({ getMock: vi.fn(), deleteMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, delete: deleteMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ canPrinter: () => true, can: () => true }) }));

const sched = (o: Partial<ScheduledDrying>): ScheduledDrying => ({
  id: 1, printer_id: 1, ams_id: 0, temp: 55, duration_hours: 8, filament: "PETG", rotate_tray: false,
  start_after: "2030-01-02T03:04:00Z", status: "pending", waiting_reason: null, error_code: null,
  started_at: null, finished_at: null, source: "manual", created_by_user_id: 1, created_at: "2030-01-01T00:00:00Z", ...o,
});

beforeEach(() => {
  getMock.mockReset().mockImplementation(async (path: string) => {
    if (path === "/printers") return [a1Mini().printer];
    if (path === "/printers/1/drying/schedules")
      return [sched({}), sched({ id: 2, status: "waiting", waiting_reason: "ams_power_required" }), sched({ id: 3, status: "running" }), sched({ id: 4, status: "done" }), sched({ id: 5, status: "failed", error_code: "ams_power_required" })];
    return [];
  });
  deleteMock.mockReset().mockResolvedValue({});
});

describe("ScheduledDryingsList", () => {
  it("shows live schedules across printers and cancels pending/waiting only", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ScheduledDryingsList />
      </QueryClientProvider>,
    );
    const rows = await screen.findAllByTestId("dry-schedule");
    expect(rows).toHaveLength(4);
        expect(screen.getAllByText("Waiting for AMS power")).toHaveLength(2);
    const cancels = screen.getAllByRole("button", { name: "Cancel" });
    expect(cancels).toHaveLength(2);
    fireEvent.click(cancels[0]);
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/drying/schedules/1"));
  });
});
