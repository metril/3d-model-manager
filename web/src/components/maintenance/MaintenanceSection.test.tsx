import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MaintenanceTask } from "@/api/types";
import { MaintenanceBadge } from "@/components/maintenance/MaintenanceBadge";
import { MaintenanceSection } from "@/components/maintenance/MaintenanceSection";

const { getMock, postMock, putMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  deleteMock: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock, delete: deleteMock } };
});

const task = (o: Partial<MaintenanceTask>): MaintenanceTask => ({
  id: 1,
  printer_id: 1,
  name: "Lube rods",
  interval_hours: 100,
  interval_days: null,
  last_done_at: null,
  notes: null,
  status: "ok",
  hours_since: 10,
  days_since: null,
  hours_remaining: 90,
  days_remaining: null,
  ...o,
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  putMock.mockReset();
  deleteMock.mockReset();
});

describe("MaintenanceSection", () => {
  it("shows due status, highlights overdue, marks done with a note", async () => {
    getMock.mockImplementation((p: string) =>
      Promise.resolve(
        p.endsWith("/maintenance")
          ? [task({}), task({ id: 2, name: "Clean nozzle", status: "overdue", hours_remaining: -5.5 })]
          : [],
      ),
    );
    postMock.mockResolvedValue(task({}));
    wrap(<MaintenanceSection printerId={1} maintenanceMode={false} canControl />);
    expect(await screen.findByText("90.0 h left")).toBeInTheDocument();
    expect(screen.getByText("5.5 h overdue")).toBeInTheDocument();
    expect(screen.getByText("Clean nozzle").closest("li")).toHaveAttribute("data-status", "overdue");
    fireEvent.click(screen.getAllByRole("button", { name: "Mark done" })[0]);
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "greased" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/maintenance/1/done", { note: "greased" }),
    );
  });

  it("adds a task and toggles maintenance mode", async () => {
    getMock.mockResolvedValue([]);
    postMock.mockResolvedValue(task({}));
    putMock.mockResolvedValue({ maintenance_mode: true });
    wrap(<MaintenanceSection printerId={1} maintenanceMode={false} canControl />);
    expect(await screen.findByText("No maintenance tasks yet.")).toBeInTheDocument();
    expect(screen.getByText(/dispatcher skips this printer/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/printers/1/maintenance/mode", { enabled: true }));
    fireEvent.click(screen.getByRole("button", { name: "Add task" }));
    fireEvent.change(screen.getByLabelText("Task name"), { target: { value: "Belt check" } });
    fireEvent.change(screen.getByLabelText("Every (days)"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Add task", hidden: false }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/maintenance", {
        name: "Belt check",
        interval_hours: null,
        interval_days: 30,
        notes: null,
      }),
    );
  });

  it("hides mutations without control", async () => {
    getMock.mockResolvedValue([task({})]);
    wrap(<MaintenanceSection printerId={1} maintenanceMode={false} canControl={false} />);
    await screen.findByText("Lube rods");
    expect(screen.queryByRole("button", { name: "Mark done" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add task" })).toBeNull();
  });
});

describe("MaintenanceBadge", () => {
  it("shows the due count, hidden when none", async () => {
    getMock.mockResolvedValue([task({ status: "soon" }), task({ id: 2, status: "overdue" }), task({ id: 3 })]);
    const { unmount } = wrap(<MaintenanceBadge printerId={1} />);
    expect(await screen.findByText("2 due")).toBeInTheDocument();
    unmount();
    getMock.mockResolvedValue([task({})]);
    const { container } = wrap(<MaintenanceBadge printerId={1} />);
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
    expect(container).toBeEmptyDOMElement();
  });
});
