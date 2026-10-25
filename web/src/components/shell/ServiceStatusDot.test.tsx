import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ServiceStatusDot } from "@/components/shell/ServiceStatusDot";
import { TooltipProvider } from "@/components/ui/tooltip";

const { getMock, authState } = vi.hoisted(() => ({ getMock: vi.fn(), authState: { admin: true } }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => authState.admin }) }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...rest }: { children: React.ReactNode } & Record<string, unknown>) => (
    <a href="/settings?tab=server" aria-label={rest["aria-label"] as string}>
      {children}
    </a>
  ),
}));

function renderDot() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <ServiceStatusDot collapsed={false} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockReset();
  authState.admin = true;
});

describe("ServiceStatusDot", () => {
  it("links to Settings -> Server when a service is down", async () => {
    getMock.mockResolvedValue({ overall: "down", checked_at: 1, services: {} });
    renderDot();
    const link = await screen.findByRole("link", { name: /A service is down/ });
    expect(link).toHaveAttribute("href", "/settings?tab=server");
  });

  it("shows the amber warning state", async () => {
    getMock.mockResolvedValue({ overall: "warn", checked_at: 1, services: {} });
    renderDot();
    expect(await screen.findByRole("link", { name: /need attention/ })).toBeInTheDocument();
  });

  it("renders nothing when healthy", async () => {
    getMock.mockResolvedValue({ overall: "ok", checked_at: 1, services: {} });
    const { container } = renderDot();
    await vi.waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("never queries for non-admins", () => {
    authState.admin = false;
    const { container } = renderDot();
    expect(getMock).not.toHaveBeenCalled();
    expect(container).toBeEmptyDOMElement();
  });
});
