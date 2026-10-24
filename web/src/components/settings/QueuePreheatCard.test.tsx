import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QueuePreheatCard } from "@/components/settings/QueuePreheatCard";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock } };
});

const SETTINGS = {
  enabled: true,
  chamber_targets: { ABS: 40 },
  bed_targets: { PLA: 60 },
  max_wait_s: 600,
  soak_s: 120,
  keep_warm_enabled: false,
  keep_warm_bed_c: 60,
  keep_warm_max_min: 60,
};

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <QueuePreheatCard />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockReset().mockResolvedValue(SETTINGS);
  putMock.mockReset().mockImplementation((_p: string, body: unknown) => Promise.resolve(body));
});

describe("QueuePreheatCard", () => {
  it("saves the edited settings with maps converted back to numbers", async () => {
    renderCard();
    fireEvent.change(await screen.findByLabelText("Max wait (s)"), { target: { value: "900" } });
    fireEvent.click(screen.getByRole("switch", { name: "Keep warm between prints" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[0]);
    fireEvent.change(screen.getByLabelText("Chamber targets (C) Filament 2"), { target: { value: "ASA" } });
    fireEvent.change(screen.getByLabelText("Chamber targets (C) Temp 2"), { target: { value: "45" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings/queue-preheat", {
        enabled: true,
        chamber_targets: { ABS: 40, ASA: 45 },
        bed_targets: { PLA: 60 },
        max_wait_s: 900,
        soak_s: 120,
        keep_warm_enabled: true,
        keep_warm_bed_c: 60,
        keep_warm_max_min: 60,
      }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("blocks save with out-of-range values", async () => {
    renderCard();
    fireEvent.change(await screen.findByLabelText("Max wait (s)"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Soak (s)"), { target: { value: "5000" } });
    fireEvent.change(screen.getByLabelText("Keep-warm bed (C)"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText("Keep-warm max (min)"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Max wait must be 60-3600")).toBeInTheDocument();
    expect(screen.getByText("Soak must be 0-1800")).toBeInTheDocument();
    expect(screen.getByText("Keep-warm bed must be 40-110")).toBeInTheDocument();
    expect(screen.getByText("Keep-warm max must be 5-480")).toBeInTheDocument();
    expect(putMock).not.toHaveBeenCalled();
  });
});
