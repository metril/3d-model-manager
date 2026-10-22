import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FinancePage } from "@/pages/FinancePage";

// Radix RadioGroup (used by DepositDialog) needs `ResizeObserver`,
// unpolyfilled in jsdom -- mirrors `ProfilesPage.test.tsx`'s own stub.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

const { getMock, postMock, putMock, authState } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  authState: { perms: ["billing:manage"] as string[] },
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock, put: putMock },
  };
});

vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({
    data: { id: 1, username: "u", role: "member", permissions: authState.perms },
    can: (p: string) => authState.perms.includes(p),
  }),
}));

const CENTER = {
  id: 1,
  name: "Shared shop",
  owner_user_id: null,
  group_id: 2,
  budget_total: 100,
  budget_monthly: 20,
  balance: 15,
  open_reserved: 1,
  month_spent: 4,
  available: 14,
  active: true,
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <FinancePage />
    </QueryClientProvider>,
  );
}

describe("FinancePage", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    putMock.mockReset();
    authState.perms = ["billing:manage"];
    getMock.mockImplementation((path: string) => {
      if (path === "/finance/config") return Promise.resolve({ billing_enabled: true });
      if (path.startsWith("/finance/cost-centers")) return Promise.resolve([CENTER]);
      if (path.startsWith("/finance/ledger")) {
        return Promise.resolve([
          {
            id: 9,
            cost_center_id: 1,
            kind: "deposit",
            amount: 25,
            print_queue_id: null,
            print_id: null,
            created_by_user_id: 1,
            note: "top up",
            created_at: "2026-01-01T00:00:00Z",
          },
        ]);
      }
      return Promise.resolve([]);
    });
  });

  it("shows manager controls (billing switch, New center, Edit, Deposit, Delete)", async () => {
    renderPage();
    expect(await screen.findByText("Shared shop")).toBeInTheDocument();
    expect(screen.getByLabelText("Billing enabled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New center" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deposit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("hides manager controls for a member, who only sees their own centers read-only", async () => {
    authState.perms = [];
    renderPage();
    expect(await screen.findByText("Shared shop")).toBeInTheDocument();
    expect(screen.queryByLabelText("Billing enabled")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New center" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith(expect.stringContaining("mine=true"));
  });

  it("clicking a row renders its ledger with signed amounts", async () => {
    renderPage();
    const row = await screen.findByText("Shared shop");
    fireEvent.click(row);
    expect(await screen.findByText("+25.00")).toBeInTheDocument();
  });

  it("posts kind and amount when depositing", async () => {
    postMock.mockResolvedValue({ id: 10, cost_center_id: 1, kind: "deposit", amount: 5, created_at: "2026-01-01T00:00:00Z" });
    renderPage();
    await screen.findByText("Shared shop");
    fireEvent.click(screen.getByRole("button", { name: "Deposit" }));
    const amountInput = await screen.findByLabelText("Amount");
    fireEvent.change(amountInput, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Post" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        "/finance/deposits",
        expect.objectContaining({ cost_center_id: 1, amount: 5, kind: "deposit" }),
      ),
    );
  });
});
