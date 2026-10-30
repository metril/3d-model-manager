import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SpoolOut } from "@/api/types";
import { SpoolForm } from "@/components/inventory/SpoolForm";

const { getMock, patchMock } = vi.hoisted(() => ({ getMock: vi.fn(), patchMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, patch: patchMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => true, canPrinter: () => true }) }));
vi.mock("@/components/ui/select", () => import("@/test/selectMock"));

const SPOOL = {
  id: 7,
  material_type: "PLA",
  label_weight_g: 1000,
  weight_used_g: 0,
  low_stock_g: 100,
  suppliers: [],
  last_dried_at: "2026-01-02T03:04:00.000Z",
  last_dried_temp_c: 55,
  last_dried_hours: 6,
} as unknown as SpoolOut;

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SpoolForm open onOpenChange={vi.fn()} spool={SPOOL} />
    </QueryClientProvider>,
  );
}

describe("SpoolForm last dried", () => {
  beforeEach(() => {
    getMock.mockReset().mockResolvedValue([]);
    patchMock.mockReset().mockResolvedValue(SPOOL);
  });

  it("omits last_dried_at when untouched", async () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    expect(patchMock.mock.calls[0][1]).not.toHaveProperty("last_dried_at");
  });

  it("sends the edited stamp as ISO", async () => {
    setup();
    fireEvent.change(screen.getByLabelText("Last dried"), { target: { value: "2026-03-04T05:06" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    expect(patchMock.mock.calls[0][1].last_dried_at).toBe(new Date("2026-03-04T05:06").toISOString());
  });
});
