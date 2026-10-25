import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ENROLL_2FA_EVENT } from "@/api/client";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});
vi.mock("@/hooks/useEvents", () => ({ EventsProvider: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("@tanstack/react-router", () => ({ Outlet: () => <div>app outlet</div>, useNavigate: () => vi.fn() }));
vi.mock("@/components/shell/AppSidebar", () => ({ AppSidebar: () => null }));
vi.mock("@/components/shell/TopBar", () => ({ TopBar: () => null }));
vi.mock("@/components/shell/DevModeBanner", () => ({ DevModeBanner: () => null }));
vi.mock("@/components/shell/CommandPalette", () => ({ CommandPalette: () => null }));
vi.mock("@/components/shell/ShortcutsDialog", () => ({ ShortcutsDialog: () => null }));
vi.mock("@/components/shell/useVisibleNav", () => ({ useVisibleNav: () => ({ ordered: [] }) }));
vi.mock("@/pages/EnrollTwoFactorPage", () => ({
  EnrollTwoFactorPage: ({ onFinished }: { onFinished: () => void }) => (
    <button type="button" onClick={onFinished}>
      gate continue
    </button>
  ),
}));

import { AppShell } from "@/components/AppShell";

let me: Record<string, unknown>;
const meCalls = () => getMock.mock.calls.filter((c) => c[0] === "/auth/me").length;

function renderShell() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  render(
    <QueryClientProvider client={qc}>
      <AppShell />
    </QueryClientProvider>,
  );
  return qc;
}

describe("AppShell 2FA enrolment gate", () => {
  beforeEach(() => {
    me = { id: 1, username: "a", permissions: [], must_enroll_2fa: true, two_factor_enabled: false };
    getMock.mockReset().mockImplementation(() => Promise.resolve(me));
  });

  it("renders the gate, keeps it after `me` flips, and releases on Continue", async () => {
    const qc = renderShell();
    expect(await screen.findByText("gate continue")).toBeInTheDocument();
    expect(screen.queryByText("app outlet")).toBeNull();
    me = { ...me, must_enroll_2fa: false, two_factor_enabled: true };
    await act(() => qc.invalidateQueries({ queryKey: ["auth", "me"] }));
    expect(screen.getByText("gate continue")).toBeInTheDocument();
    fireEvent.click(screen.getByText("gate continue"));
    expect(await screen.findByText("app outlet")).toBeInTheDocument();
  });

  it("releases the gate when require_2fa is switched off mid-session", async () => {
    const qc = renderShell();
    await screen.findByText("gate continue");
    me = { ...me, must_enroll_2fa: false, two_factor_enabled: false };
    await act(() => qc.invalidateQueries({ queryKey: ["auth", "me"] }));
    expect(await screen.findByText("app outlet")).toBeInTheDocument();
  });

  it("refetches me on the 403 event only when the gate isn't showing", async () => {
    me = { ...me, must_enroll_2fa: false, two_factor_enabled: true };
    renderShell();
    await screen.findByText("app outlet");
    const before = meCalls();
    act(() => {
      window.dispatchEvent(new Event(ENROLL_2FA_EVENT));
    });
    await waitFor(() => expect(meCalls()).toBe(before + 1));
  });

  it("ignores the 403 event while the gate is up", async () => {
    renderShell();
    await screen.findByText("gate continue");
    const before = meCalls();
    act(() => {
      window.dispatchEvent(new Event(ENROLL_2FA_EVENT));
    });
    await new Promise((r) => setTimeout(r, 50));
    expect(meCalls()).toBe(before);
  });
});
