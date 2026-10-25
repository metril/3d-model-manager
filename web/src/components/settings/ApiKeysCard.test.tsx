import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiKeysCard } from "@/components/settings/ApiKeysCard";
import { ENDPOINTS, curlFor } from "@/components/settings/apiKeysDocs";

const { getMock, postMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, delete: deleteMock } };
});

const writeText = vi.fn().mockResolvedValue(undefined);

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ApiKeysCard />
    </QueryClientProvider>,
  );
}

const KEY = {
  id: 1,
  label: "Home Assistant",
  key_prefix: "tdmm_abcde",
  scopes: ["read", "control"],
  printer_ids: null,
  expires_at: null,
  rate_limit_per_min: 120,
  created_at: "2026-10-01T00:00:00Z",
  last_used_at: null,
  user_id: 1,
  username: "admin",
};

describe("ApiKeysCard", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    deleteMock.mockReset();
    writeText.mockClear();
    Object.assign(navigator, { clipboard: { writeText } });
    getMock.mockImplementation((path: string) =>
      Promise.resolve(path === "/printers" ? [{ id: 7, name: "A1 mini" }] : [KEY]),
    );
  });

  it("lists keys with scopes and never a secret", async () => {
    renderCard();
    expect(await screen.findByText("Home Assistant")).toBeInTheDocument();
    expect(screen.getByText(/read, control/)).toBeInTheDocument();
    expect(screen.getByText(/tdmm_abcde/)).toBeInTheDocument();
  });

  it("creates a key with chosen scopes/allowlist and shows the secret once", async () => {
    postMock.mockResolvedValue({ ...KEY, id: 2, label: "CI", key: "tdmm_SECRETVALUE" });
    renderCard();
    await screen.findByText("Home Assistant");

    fireEvent.change(screen.getByLabelText("Key label"), { target: { value: "CI" } });
    fireEvent.click(screen.getByLabelText(/Manage queue/));
    fireEvent.click(await screen.findByLabelText("A1 mini"));
    fireEvent.change(screen.getByLabelText("Expires"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));

    await waitFor(() => expect(postMock).toHaveBeenCalled());
    expect(postMock.mock.calls[0][0]).toBe("/settings/api-keys");
    expect(postMock.mock.calls[0][1]).toMatchObject({
      label: "CI",
      scopes: ["read", "queue"],
      printer_ids: [7],
      expires_in_days: 30,
    });
    expect(await screen.findByLabelText("New API key")).toHaveTextContent("tdmm_SECRETVALUE");
    fireEvent.click(screen.getByRole("button", { name: /Done/ }));
    expect(screen.queryByText("tdmm_SECRETVALUE")).not.toBeInTheDocument();
  });

  it("revokes a key", async () => {
    deleteMock.mockResolvedValue(undefined);
    renderCard();
    await screen.findByText("Home Assistant");
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/settings/api-keys/1"));
  });

  it("documents every endpoint with a curl example", async () => {
    renderCard();
    await screen.findByText("Home Assistant");
    expect(screen.getByText("API reference")).toBeInTheDocument();
    for (const e of ENDPOINTS) {
      expect(curlFor(e, "http://x")).toContain('-H "X-API-Key: $TDMM_KEY"');
    }
    expect(curlFor(ENDPOINTS[2], "http://x")).toBe(
      'curl -X POST -H "X-API-Key: $TDMM_KEY" "http://x/api/ext/v2/printers/1/pause"',
    );
  });
});
