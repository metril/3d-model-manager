import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FailureDetectionCard } from "@/components/settings/FailureDetectionCard";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => false }) }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="/settings?tab=server">{children}</a>,
}));

const SETTINGS = {
  url: "",
  enabled: true,
  interval_s: 10,
  warn: 0.38,
  pause: 0.78,
  action: "notify",
  callback_url: "http://api:8080",
};

beforeEach(() => {
  getMock.mockReset();
  getMock.mockResolvedValue(SETTINGS);
});

describe("FailureDetectionCard", () => {
  it("is a pointer to Settings -> Server with the current state, not an editor", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <FailureDetectionCard />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("On")).toBeInTheDocument();
    expect(screen.getByText(/Warn at 0.38, pause at 0.78/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Pause threshold")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.getByText(/Enable it per printer in Printer setup/)).toBeInTheDocument();
  });
});
