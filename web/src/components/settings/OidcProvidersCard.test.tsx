import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OidcProvidersCard } from "@/components/settings/OidcProvidersCard";

const { getMock, patchMock } = vi.hoisted(() => ({ getMock: vi.fn(), patchMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, patch: patchMock } };
});

const provider = {
  id: 3, name: "Authentik", slug: "authentik", discovery_url: "https://sso.example/.well-known/openid-configuration",
  client_id: "cid", has_client_secret: true, scopes: "openid", groups_claim: "groups", group_mapping: {},
  admin_group: null, auto_create_users: false, link_by_email: false, enabled: true, trusts_idp_mfa: false,
  callback_url: "https://tdmm.example/api/auth/oidc/authentik/callback",
};

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OidcProvidersCard />
    </QueryClientProvider>,
  );
}

describe("OidcProvidersCard", () => {
  beforeEach(() => {
    getMock.mockReset().mockResolvedValue([provider]);
    patchMock.mockReset().mockResolvedValue(provider);
  });

  it("omits client_secret from PATCH when left blank", async () => {
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Edit Authentik" }));
    expect(await screen.findByPlaceholderText("Leave blank to keep current secret")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    expect(patchMock.mock.calls[0][1]).not.toHaveProperty("client_secret");
  });

  it("shows the server-provided callback URL", async () => {
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Edit Authentik" }));
    expect(await screen.findByText(/\/api\/auth\/oidc\/authentik\/callback$/)).toBeInTheDocument();
  });

  it("sends trusts_idp_mfa when the switch is toggled", async () => {
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Edit Authentik" }));
    fireEvent.click(await screen.findByRole("switch", { name: "Trust identity provider MFA" }));
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    expect(patchMock.mock.calls[0][1]).toMatchObject({ trusts_idp_mfa: true });
  });
});
