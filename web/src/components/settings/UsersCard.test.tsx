import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { UsersCard } from "@/components/settings/UsersCard";

const { getMock, patchMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), patchMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, patch: patchMock, post: postMock } };
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
    postMock.mockReset().mockResolvedValue(undefined);
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

  it("resets 2FA after confirmation", async () => {
    renderCard();
    await screen.findByText("bob");
    fireEvent.click(screen.getByRole("button", { name: "Reset 2FA" }));
    expect(postMock).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Reset 2FA" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/admin/users/2/2fa/reset"));
  });

  it("hides Reset 2FA on the admin's own row", async () => {
    getMock.mockImplementation((path: string) =>
      Promise.resolve(path === "/auth/me" ? { id: 2, permissions: [] } : [bob]),
    );
    renderCard();
    await screen.findByText("bob");
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/auth/me"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Reset 2FA" })).toBeNull());
  });
});
