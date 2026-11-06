import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BackupsCard } from "@/components/settings/BackupsCard";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => true }) }));

const ops = (hours: number) => ({
  backup_enabled: true,
  backup_interval_hours: hours,
  git_provider: null,
  git_base_url: null,
  git_repo: null,
  git_branch: "main",
  git_token_set: false,
});

describe("BackupsCard", () => {
  beforeEach(() => {
    getMock.mockReset().mockImplementation((path: string) =>
      Promise.resolve(path === "/settings/ops" ? ops(12) : []),
    );
  });

  it("seeds the draft once: a refetch does not clobber in-progress edits", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <BackupsCard />
      </QueryClientProvider>,
    );
    const input = (await screen.findByLabelText("Interval (hours)")) as HTMLInputElement;
    await vi.waitFor(() => expect(input.value).toBe("12"));
    fireEvent.change(input, { target: { value: "6" } });
    expect(input.value).toBe("6");

    getMock.mockImplementation((path: string) => Promise.resolve(path === "/settings/ops" ? ops(48) : []));
    await act(async () => {
      await qc.refetchQueries();
    });
    expect(input.value).toBe("6");
  });
});
