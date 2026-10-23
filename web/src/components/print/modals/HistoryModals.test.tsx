import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AmsHistoryModal } from "@/components/print/modals/AmsHistoryModal";
import { HeaterHistoryModal } from "@/components/print/modals/HeaterHistoryModal";

const get = vi.fn();
vi.mock("@/api/client", () => ({ api: { get: (...a: unknown[]) => get(...a) } }));

const wrap = (ui: React.ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

beforeEach(() => get.mockReset());

describe("HeaterHistoryModal", () => {
  it("charts nozzle and bed, omits unreported chamber", async () => {
    get.mockResolvedValue({
      step_s: 60,
      points: [
        { ts: "2026-10-23T10:00:00Z", nozzle: 200, nozzle_target: 210, bed: 60, bed_target: 60, chamber: null, fan_part: 0 },
        { ts: "2026-10-23T10:01:00Z", nozzle: 210, nozzle_target: 210, bed: 60, bed_target: 60, chamber: null, fan_part: 0 },
      ],
    });
    wrap(<HeaterHistoryModal printerId={3} open onOpenChange={() => {}} />);
    expect(await screen.findByRole("img", { name: "Temperature history" })).toBeInTheDocument();
    expect(document.querySelector('[data-series="nozzle"]')).not.toBeNull();
    expect(document.querySelector('[data-series="bed"]')).not.toBeNull();
    expect(document.querySelector('[data-series="chamber"]')).toBeNull();
    expect(get.mock.calls[0][0]).toMatch(/^\/printers\/3\/sensors\?from=/);
  });

  it("shows an empty state", async () => {
    get.mockResolvedValue({ step_s: 60, points: [] });
    wrap(<HeaterHistoryModal printerId={3} open onOpenChange={() => {}} />);
    expect(await screen.findByText("No samples in this range.")).toBeInTheDocument();
  });

  it("does not fetch while closed", () => {
    wrap(<HeaterHistoryModal printerId={3} open={false} onOpenChange={() => {}} />);
    expect(get).not.toHaveBeenCalled();
  });
});

describe("AmsHistoryModal", () => {
  it("renders humidity and temperature charts per unit", async () => {
    get.mockResolvedValue({
      unit: 0,
      step_s: 60,
      points: [
        { ts: "2026-10-23T10:00:00Z", humidity: 30, temp: 25 },
        { ts: "2026-10-23T10:05:00Z", humidity: 32, temp: 26 },
      ],
    });
    wrap(<AmsHistoryModal printerId={3} units={[0]} open onOpenChange={() => {}} />);
    expect(await screen.findByRole("img", { name: /AMS unit 1 - Humidity/ })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: /AMS unit 1 - Temperature/ })).toBeInTheDocument();
    expect(get.mock.calls[0][0]).toMatch(/^\/printers\/3\/ams-history\?unit=0&from=/);
  });
});
