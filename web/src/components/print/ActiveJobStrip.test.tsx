import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ActiveJobOut, PrinterStatusOut } from "@/api/types";
import { ActiveJobStrip } from "@/components/print/ActiveJobStrip";

const { commandMock } = vi.hoisted(() => ({ commandMock: vi.fn() }));

vi.mock("@/api/printers", () => ({
  usePrinterCommand: () => ({ mutate: commandMock, isPending: false }),
}));

function job(overrides: Partial<ActiveJobOut> = {}): ActiveJobOut {
  return {
    id: 1,
    printer_id: 1,
    file_id: 10,
    origin: "app",
    subtask_name: "dragon.gcode.3mf",
    state: "printing",
    progress_pct: 42,
    remaining_min: 65,
    layer: 10,
    total_layers: 120,
    printer_error: null,
    created_at: "2026-10-22T10:00:00Z",
    started_at: "2026-10-22T10:00:00Z",
    finished_at: null,
    plate: 1,
    model_id: 7,
    model_slug: "articulated-dragon",
    model_name: "Articulated Dragon",
    ...overrides,
  };
}

function status(overrides: Partial<PrinterStatusOut> = {}): PrinterStatusOut {
  return {
    online: true,
    gcode_state: "RUNNING",
    mc_percent: 42,
    layer_num: 10,
    total_layer_num: 120,
    mc_remaining_time: 65,
    print_error: null,
    nozzle_temper: 215,
    bed_temper: 60,
    subtask_name: "dragon.gcode.3mf",
    wifi_signal: null,
    trays: [],
    ...overrides,
  };
}

beforeEach(() => {
  commandMock.mockClear();
});

describe("ActiveJobStrip", () => {
  it("renders progress, layer, and ETA for a RUNNING job, with an amber bar", () => {
    render(<ActiveJobStrip job={job()} status={status()} printerId={1} canControl={true} />);

    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(bar.querySelector(".bg-state-printing")).toBeTruthy();
    expect(screen.getByText("Layer 10/120")).toBeInTheDocument();
    expect(screen.getByText(/ETA/)).toHaveTextContent("ETA 1h 05m");
  });

  it("shows the model name and plate", () => {
    render(<ActiveJobStrip job={job()} status={status()} printerId={1} canControl={true} />);

    expect(screen.getByText("Articulated Dragon")).toBeInTheDocument();
    expect(screen.getByText("Plate 1")).toBeInTheDocument();
  });

  it("shows the per-job estimated cost with the shared cost format", () => {
    render(
      <ActiveJobStrip job={job({ estimated_cost: 1.5 })} status={status()} printerId={1} canControl={true} />,
    );

    expect(screen.getByText("Est. cost 1.50")).toBeInTheDocument();
  });

  it("falls back to subtask_name when model_name is null", () => {
    render(
      <ActiveJobStrip
        job={job({ model_name: null, model_slug: null })}
        status={status()}
        printerId={1}
        canControl={true}
      />,
    );

    expect(screen.getByText("dragon.gcode.3mf")).toBeInTheDocument();
  });

  it("hides Pause/Stop when canControl is false", () => {
    render(<ActiveJobStrip job={job()} status={status()} printerId={1} canControl={false} />);

    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
  });

  it("shows Pause when canControl is true and it posts /printers/1/pause", async () => {
    render(<ActiveJobStrip job={job()} status={status()} printerId={1} canControl={true} />);

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));

    await waitFor(() => expect(commandMock).toHaveBeenCalledExactlyOnceWith("pause"));
  });

  // Fix-review finding 5: canControl alone used to be enough to show
  // Pause/Stop -- a printer that just went offline (stale RUNNING status)
  // or finished (FINISH) must hide them too.
  it("hides Pause/Stop when canControl is true but the printer is offline", () => {
    render(<ActiveJobStrip job={job()} status={status({ online: false })} printerId={1} canControl={true} />);

    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
  });

  it("hides Pause/Stop once gcode_state is a non-printing, non-paused tone (e.g. FINISH)", () => {
    render(
      <ActiveJobStrip job={job()} status={status({ gcode_state: "FINISH" })} printerId={1} canControl={true} />,
    );

    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
  });

  it("shows Resume instead of Pause while paused", () => {
    render(
      <ActiveJobStrip job={job()} status={status({ gcode_state: "PAUSE" })} printerId={1} canControl={true} />,
    );

    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
  });

  it("Stop requires confirming before it posts /printers/1/stop", async () => {
    render(<ActiveJobStrip job={job()} status={status()} printerId={1} canControl={true} />);

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(commandMock).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(commandMock).toHaveBeenCalledExactlyOnceWith("stop"));
  });
});
