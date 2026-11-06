import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { BrowserExtensionCard } from "@/components/settings/BrowserExtensionCard";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as PrintablesAccountCard.test.tsx/StorageBackendsCard's
// covering test), so the fakes have to be created through `vi.hoisted`.
const { getMock, postMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock, delete: deleteMock },
  };
});

const writeText = vi.fn().mockResolvedValue(undefined);

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserExtensionCard />
    </QueryClientProvider>,
  );
}

describe("BrowserExtensionCard", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    deleteMock.mockReset();
    writeText.mockClear();
    Object.assign(navigator, { clipboard: { writeText } });
  });

  it("shows the empty state when there are no tokens", async () => {
    getMock.mockResolvedValue([]);

    renderCard();

    expect(await screen.findByText("No tokens yet.")).toBeInTheDocument();
  });

  it("lists existing tokens with their created/last-used state, never a token value", async () => {
    getMock.mockResolvedValue([
      { id: 1, label: "Chrome laptop", created_at: "2026-07-01T00:00:00Z", last_used_at: "2026-07-05T00:00:00Z" },
      { id: 2, label: "Chrome desktop", created_at: "2026-07-02T00:00:00Z", last_used_at: null },
    ]);

    renderCard();

    expect(await screen.findByText("Chrome laptop")).toBeInTheDocument();
    expect(screen.getByText("Chrome desktop")).toBeInTheDocument();
    expect(screen.getByText(/Last used/)).toBeInTheDocument();
    expect(screen.getByText(/Never used/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(2);
  });

  it("mints a token, reveals the plaintext once, and refreshes the list on dismiss", async () => {
    getMock.mockResolvedValueOnce([]).mockResolvedValueOnce([
      { id: 9, label: "New extension token", created_at: "2026-07-10T00:00:00Z", last_used_at: null },
    ]);
    postMock.mockResolvedValue({
      id: 9,
      label: "New extension token",
      token: "tdmm_secret_plaintext",
      created_at: "2026-07-10T00:00:00Z",
    });

    renderCard();
    await screen.findByText("No tokens yet.");

    fireEvent.change(screen.getByLabelText("Token label"), { target: { value: "New extension token" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/settings/api-tokens", { label: "New extension token" }),
    );

    expect(await screen.findByText("tdmm_secret_plaintext")).toBeInTheDocument();
    // The mint form is replaced by the reveal, so a second mint can't happen
    // while the plaintext is still showing.
    expect(screen.queryByLabelText("Token label")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Done — I've saved it" }));

    expect(screen.queryByText("tdmm_secret_plaintext")).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Token label")).toBeInTheDocument();
  });

  it("copies the revealed token to the clipboard", async () => {
    getMock.mockResolvedValue([]);
    postMock.mockResolvedValue({
      id: 9,
      label: "New extension token",
      token: "tdmm_secret_plaintext",
      created_at: "2026-07-10T00:00:00Z",
    });

    renderCard();
    await screen.findByText("No tokens yet.");

    fireEvent.change(screen.getByLabelText("Token label"), { target: { value: "New extension token" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    await screen.findByText("tdmm_secret_plaintext");

    fireEvent.click(screen.getByRole("button", { name: /Copy/ }));

    expect(writeText).toHaveBeenCalledWith("tdmm_secret_plaintext");
  });

  it("surfaces a mint failure message", async () => {
    getMock.mockResolvedValue([]);
    postMock.mockRejectedValue(new ApiError(422, "label must not be empty"));

    renderCard();
    await screen.findByText("No tokens yet.");

    fireEvent.change(screen.getByLabelText("Token label"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("label must not be empty");
  });

  it("revokes a token by id when Revoke is clicked", async () => {
    getMock.mockResolvedValue([
      { id: 3, label: "Old token", created_at: "2026-06-01T00:00:00Z", last_used_at: null },
    ]);
    deleteMock.mockResolvedValue(undefined);

    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/settings/api-tokens/3"));
  });

  it("surfaces a revoke failure message", async () => {
    getMock.mockResolvedValue([
      { id: 3, label: "Old token", created_at: "2026-06-01T00:00:00Z", last_used_at: null },
    ]);
    deleteMock.mockRejectedValue(new ApiError(404, "api token 3 not found"));

    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Revoke" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("api token 3 not found");
  });
});
