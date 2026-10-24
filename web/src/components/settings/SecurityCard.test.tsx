import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SecurityCard } from "@/components/settings/SecurityCard";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock } };
});
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

describe("SecurityCard", () => {
  beforeEach(() => {
    getMock.mockReset().mockResolvedValue({ require_2fa: false });
    putMock.mockReset().mockResolvedValue({ require_2fa: true });
  });

  it("toggles require_2fa via PUT /settings/security", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <SecurityCard />
      </QueryClientProvider>,
    );
    const sw = await screen.findByRole("switch", { name: "Require two-factor for all users" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    fireEvent.click(sw);
    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/settings/security", { require_2fa: true }));
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "true"));
  });
});
