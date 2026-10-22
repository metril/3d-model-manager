import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { UsersCard } from "@/components/settings/UsersCard";

const { getMock, patchMock } = vi.hoisted(() => ({ getMock: vi.fn(), patchMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, patch: patchMock } };
});

const bob = {
  id: 2, username: "bob", display_name: null, email: null, role: "member", is_active: true,
  has_password: false, oidc_linked: true, last_login_at: null, groups: [],
};

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <UsersCard />
    </QueryClientProvider>,
  );
}

describe("UsersCard", () => {
  beforeEach(() => {
    getMock.mockReset().mockResolvedValue([bob]);
    patchMock.mockReset();
  });

  it("lists users with role and SSO badge", async () => {
    renderCard();
    expect(await screen.findByText("bob")).toBeInTheDocument();
    expect(screen.getByText("SSO")).toBeInTheDocument();
    expect(screen.getByLabelText("Role for bob")).toBeInTheDocument();
  });

  it("shows the 409 detail when demoting the last admin", async () => {
    patchMock.mockRejectedValue(new ApiError(409, "cannot remove the last admin"));
    renderCard();
    await screen.findByText("bob");
    fireEvent.change(screen.getByLabelText("Role for bob"), { target: { value: "viewer" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot remove the last admin");
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("/admin/users/2", { role: "viewer" }));
  });
});
