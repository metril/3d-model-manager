import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import type { AmsTray, PrinterCapabilities, PrinterOverviewItem, PrinterStatusOut } from "@/api/types";

import { NO_CAPS } from "./caps";

export const A1_CAPS: PrinterCapabilities = { ...NO_CAPS, model_key: "a1mini", camera: "a1", max_bed: 80, max_nozzle: 300, verified: true };
/** Mirrors backend capabilities.py: X1C has chamber temp but NO heater and no drying. */
export const X1C_CAPS: PrinterCapabilities = {
  ...NO_CAPS,
  model_key: "x1c",
  chamber_temp: true,
  door: true,
  aux_fan: true,
  chamber_fan: true,
  camera: "rtsp",
  lidar: true,
  max_bed: 120,
  max_nozzle: 300,
  verified: false,
};
/** X1E: the only X1 with a chamber heater. */
export const X1E_CAPS: PrinterCapabilities = { ...X1C_CAPS, model_key: "x1e", chamber_heater: true, max_nozzle: 320, ams_drying: true };

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

export function x1e(over: Partial<PrinterOverviewItem> = {}, st: Partial<PrinterStatusOut> = {}): PrinterOverviewItem {
  const base = x1c(over, st);
  return {
    ...base,
    printer: { ...base.printer, id: 3, name: "X1E", model: "X1E", serial: "03W09A000000003", capabilities: X1E_CAPS },
  };
}

/** Mirrors backend capabilities.py: H2D is dual nozzle and (like all H2) unverified. */
export const H2D_CAPS: PrinterCapabilities = { ...X1E_CAPS, model_key: "h2d", dual_nozzle: true, airduct: true, max_nozzle: 350 };

export function h2d(over: Partial<PrinterOverviewItem> = {}, st: Partial<PrinterStatusOut> = {}): PrinterOverviewItem {
  const base = x1c(over, st);
  return {
    ...base,
    printer: { ...base.printer, id: 4, name: "H2D", model: "H2D", serial: "09400A000000004", capabilities: H2D_CAPS },
    status: status({
      active_extruder: 0,
      extruders: [
        { id: 0, side: "right", temp: 215, target: 220, heating: true, diameter: 0.4, type: "hardened_steel" },
        { id: 1, side: "left", temp: 31, target: 0, heating: false, diameter: 0.6, type: "stainless_steel" },
      ],
      ...st,
    }),
  };
}
