import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DryingSettings } from "@/api/types";
import { DryingSettingsCard } from "./DryingSettingsCard";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock } };
});
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const SETTINGS: DryingSettings = {
  presets: { PLA: { n3f_temp: 45, n3s_temp: 45, n3f_hours: 12, n3s_hours: 12 } },
  ams_humidity_good: 40,
  ams_humidity_fair: 60,
  per_type_thresholds: { default: 60 },
  ambient_drying_enabled: false,
  ambient_sustained_min: 30,
  queue_drying_enabled: false,
  queue_drying_block: false,
  print_drying_enabled: false,
};

beforeEach(() => {
  getMock.mockReset().mockResolvedValue(SETTINGS);
  putMock.mockReset().mockImplementation(async (_p: string, b: unknown) => b);
});

function setup() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DryingSettingsCard />
    </QueryClientProvider>,
  );
}

describe("DryingSettingsCard", () => {
  it("saves the edited payload", async () => {
    setup();
    fireEvent.change(await screen.findByLabelText("PLA AMS HT °C"), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("switch", { name: "Dry automatically when humidity stays high" }));
    fireEvent.change(screen.getByLabelText("New filament"), { target: { value: "PET" } });
    fireEvent.click(screen.getByRole("button", { name: "Add filament" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const [path, body] = putMock.mock.calls[0];
    expect(path).toBe("/settings/drying");
    expect(body.presets.PLA.n3s_temp).toBe(50);
    expect(body.presets.PET).toBeDefined();
    expect(body.ambient_drying_enabled).toBe(true);
  });

  it("blocks save on invalid values", async () => {
    setup();
    fireEvent.change(await screen.findByLabelText("PLA AMS 2 Pro °C"), { target: { value: "99" } });
    expect(screen.getByRole("alert")).toHaveTextContent("PLA: temperature must be 45-85");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("PLA AMS 2 Pro °C"), { target: { value: "60" } });
    fireEvent.change(screen.getByLabelText("Humidity good below (%)"), { target: { value: "90" } });
    expect(screen.getByRole("alert")).toHaveTextContent("good must not exceed fair");
  });
});
