import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import type { AmsTray, PrinterCapabilities, PrinterOverviewItem, PrinterStatusOut } from "@/api/types";

import { NO_CAPS } from "./caps";

export const A1_CAPS: PrinterCapabilities = { ...NO_CAPS, model_key: "a1mini", camera: "a1", max_bed: 80, max_nozzle: 300, verified: true };
export const X1C_CAPS: PrinterCapabilities = {
  ...NO_CAPS,
  model_key: "x1c",
  chamber_heater: true,
  chamber_temp: true,
  airduct: false,
  door: true,
  aux_fan: true,
  chamber_fan: true,
  camera: "rtsp",
  lidar: true,
  max_bed: 120,
  max_nozzle: 300,
  ams_drying: true,
  verified: false,
};

export function tray(overrides: Partial<AmsTray> = {}): AmsTray {
  return {
    slot: 0,
    ams_unit: 0,
    tray: 0,
    color: "#FF0000",
    material: "PLA",
    tray_type: "PLA",
    tray_uuid: null,
    remain_pct: 80,
    spool: null,
    ...overrides,
  };
}

export function status(overrides: Partial<PrinterStatusOut> = {}): PrinterStatusOut {
  return {
    online: true,
    gcode_state: "IDLE",
    mc_percent: null,
    layer_num: null,
    total_layer_num: null,
    mc_remaining_time: null,
    print_error: null,
    nozzle_temper: 25,
    bed_temper: 24,
    subtask_name: null,
    wifi_signal: "-48dBm",
    trays: [],
    nozzle_target: 0,
    bed_target: 0,
    fan_part_pct: 0,
    light_on: false,
    speed_level: 2,
    stage: "Idle",
    hms: [],
    ams_units: [],
    dev_mode: true,
    ...overrides,
  };
}

export function a1Mini(over: Partial<PrinterOverviewItem> = {}, st: Partial<PrinterStatusOut> = {}): PrinterOverviewItem {
  return {
    printer: {
      id: 1,
      name: "A1 Mini",
      kind: "bambu_lan",
      host: "10.0.0.5",
      serial: "03039A000000001",
      model: "A1 mini",
      enabled: true,
      options: { nozzle_diameter: 0.4 },
      access_code_set: true,
      build_volume_mm: null,
      capabilities: A1_CAPS,
      total_print_hours: 123.4,
    },
    status: status(st),
    active_job: null,
    last_runs: [],
    can_print: true,
    can_control: true,
    up_next: [],
    ...over,
  };
}

export function x1c(over: Partial<PrinterOverviewItem> = {}, st: Partial<PrinterStatusOut> = {}): PrinterOverviewItem {
  const base = a1Mini();
  return {
    ...base,
    printer: { ...base.printer, id: 2, name: "X1C", model: "X1 Carbon", serial: "00M09A000000002", capabilities: X1C_CAPS },
    status: status({ chamber_temper: 31, chamber_target: 0, fan_aux_pct: 20, fan_chamber_pct: 10, ...st }),
    ...over,
  };
}

/** Renders `ui` inside a QueryClient + memory router (for <Link>/useNavigate). */
export function renderCard(ui: ReactElement, path = "/") {
  const rootRoute = createRootRoute();
  const index = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => ui });
  const detail = createRoute({ getParentRoute: () => rootRoute, path: "/printers/$id", component: () => null });
  const settings = createRoute({ getParentRoute: () => rootRoute, path: "/settings", component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([index, detail, settings]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  };
}
