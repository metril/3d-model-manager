import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LedgerEntry } from "@/api/types";
import { LedgerTable } from "@/components/finance/LedgerTable";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, search, ...rest }: { children: React.ReactNode; to: string; search?: unknown }) => (
    <a href={to} data-search={JSON.stringify(search)} {...rest}>
      {children}
    </a>
  ),
}));

const entry = (over: Partial<LedgerEntry>): LedgerEntry => ({
  id: 1,
  cost_center_id: 1,
  kind: "deposit",
  amount: 10,
  print_queue_id: null,
  print_id: null,
  created_by_user_id: null,
  created_by_username: null,
  note: null,
  created_at: "2026-11-01T10:00:00Z",
  ...over,
});

function renderTable() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <LedgerTable costCenterId={1} />
    </QueryClientProvider>,
  );
}

describe("LedgerTable", () => {
  beforeEach(() => getMock.mockReset());

  it("signs and colours from the stored amount, not the kind", async () => {
    getMock.mockResolvedValue([
      entry({ id: 1, kind: "adjustment", amount: 5 }), // positive adjustment is a credit
      entry({ id: 2, kind: "adjustment", amount: -3 }),
      entry({ id: 3, kind: "reservation", amount: -2 }),
      entry({ id: 4, kind: "release", amount: 2 }),
    ]);
    renderTable();
    const credit = await screen.findByText(/^\+.*5/);
    expect(credit.className).toContain("text-state-finished");
    const debit = screen.getByText(/^-.*3/);
    expect(debit.className).not.toContain("text-state-finished");
    expect(screen.getAllByText(/^\+/)).toHaveLength(2);
  });

  it("shows the username and deep-links the queue entry", async () => {
    getMock.mockResolvedValue([
      entry({ created_by_user_id: 7, created_by_username: "alice", print_queue_id: 42 }),
    ]);
    renderTable();
    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.queryByText("7")).toBeNull();
    const link = screen.getByRole("link", { name: "Queue #42" });
    expect(link.getAttribute("data-search")).toBe(JSON.stringify({ entry: 42 }));
  });
});
