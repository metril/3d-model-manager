import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AmsTray, PrinterOverviewItem } from "@/api/types";
import { PrinterCard } from "@/components/print/PrinterCard";

function tray(overrides: Partial<AmsTray> = {}): AmsTray {
  return {
    slot: 0,
    ams_unit: null,
    tray: null,
    color: null,
    material: null,
    tray_type: null,
    tray_uuid: null,
    remain_pct: null,
    spool: null,
    ...overrides,
  };
}

function item(overrides: Partial<PrinterOverviewItem> = {}): PrinterOverviewItem {
  return {
    printer: {
      id: 1,
      name: "Bambu A1",
      kind: "bambu_lan",
      host: "192.168.1.50",
      serial: "AC12345",
      model: "A1 mini",
      enabled: true,
      options: {},
      access_code_set: true,
      build_volume_mm: null,
    },
    status: {
      online: true,
      gcode_state: "IDLE",
      mc_percent: null,
      layer_num: null,
      total_layer_num: null,
      mc_remaining_time: null,
      print_error: null,
      nozzle_temper: 215,
      bed_temper: 220,
      subtask_name: null,
      wifi_signal: null,
      trays: [],
    },
    active_job: null,
    last_runs: [],
    can_print: true,
    can_control: true,
    ...overrides,
  };
}

function renderCard(ui: React.ReactElement) {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => ui });
  const detailRoute = createRoute({ getParentRoute: () => rootRoute, path: "/printers/$id", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, detailRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return render(<RouterProvider router={router} />);
}

describe("PrinterCard", () => {
  it("links the header to the printer's detail page", async () => {
    renderCard(<PrinterCard item={item()} />);

    const link = await screen.findByRole("link", { name: "Bambu A1" });
    expect(link).toHaveAttribute("href", "/printers/1");
  });

  it("shows an Offline badge when the printer is offline", async () => {
    renderCard(<PrinterCard item={item({ status: { ...item().status, online: false } })} />);

    expect(await screen.findByText("Offline")).toBeInTheDocument();
  });

  it("shows nozzle/bed temps with muted units", async () => {
    renderCard(<PrinterCard item={item()} />);

    expect(await screen.findByText(/215 \/ 220/)).toBeInTheDocument();
  });

  it("renders a filament chip per AMS tray", async () => {
    renderCard(
      <PrinterCard
        item={item({
          status: {
            ...item().status,
            trays: [
              tray({ slot: 0, color: "#FF0000", material: "PLA" }),
              tray({ slot: 1, color: "#00FF00", material: "PETG" }),
            ],
          },
        })}
      />,
    );

    await screen.findByRole("link", { name: "Bambu A1" });
    const chips = document.querySelectorAll('[data-slot="filament-chip"]');
    expect(chips).toHaveLength(2);
  });

  it("shows Idle when there's no active job", async () => {
    renderCard(<PrinterCard item={item()} />);

    await screen.findByRole("link", { name: "Bambu A1" });
    expect(screen.getAllByText("Idle").length).toBeGreaterThan(0);
  });

  it("renders the badge slot", async () => {
    renderCard(<PrinterCard item={item()} badge={<span>Primary</span>} />);

    expect(await screen.findByText("Primary")).toBeInTheDocument();
  });

  it("renders the camera slot in place of the placeholder", async () => {
    renderCard(<PrinterCard item={item()} camera={<span>Live feed</span>} />);

    expect(await screen.findByText("Live feed")).toBeInTheDocument();
  });
});
