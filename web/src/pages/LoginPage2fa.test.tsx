import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

import { LoginPage } from "@/pages/LoginPage";

function renderLogin(entry = "/login") {
  const rootRoute = createRootRoute();
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/login",
    component: LoginPage,
    validateSearch: (s: Record<string, unknown>) => ({
      error: typeof s.error === "string" ? s.error : undefined,
      step: typeof s.step === "string" ? s.step : undefined,
      token: typeof s.token === "string" ? s.token : undefined,
      methods: typeof s.methods === "string" ? s.methods : undefined,
    }),
  });
  const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => <p>home</p> });
  const router = createRouter({
    routeTree: rootRoute.addChildren([loginRoute, homeRoute]),
    history: createMemoryHistory({ initialEntries: [entry] }),
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

async function signIn() {
  fireEvent.change(await screen.findByLabelText("Username"), { target: { value: "alice" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

const need2fa = { requires_2fa: true, pre_auth_token: "tok1", methods: ["totp", "email", "backup"] };

describe("LoginPage 2FA step", () => {
  beforeEach(() => {
    getMock.mockReset().mockResolvedValue([]);
    postMock.mockReset();
  });

  it("goes straight home on a 204 login", async () => {
    postMock.mockResolvedValue(undefined);
    renderLogin();
    await signIn();
    expect(await screen.findByText("home")).toBeInTheDocument();
  });

  it("shows method tabs, auto-submits a 6-digit totp code and signs in", async () => {
    postMock.mockImplementation((path: string) =>
      Promise.resolve(path === "/auth/login" ? need2fa : { id: 1, return_to: null }),
    );
    renderLogin();
    await signIn();
    expect(await screen.findByRole("tab", { name: "Authenticator app" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Email codes" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "123456" } });
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/auth/2fa/verify", { pre_auth_token: "tok1", method: "totp", code: "123456" }),
    );
    expect(await screen.findByText("home")).toBeInTheDocument();
  });

  it("only offers the methods the server listed", async () => {
    postMock.mockResolvedValue({ requires_2fa: true, pre_auth_token: "t", methods: ["totp"] });
    renderLogin();
    await signIn();
    await screen.findByLabelText("Code");
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByText("Use a backup code")).toBeNull();
  });

  it("sends an email code and maps the rate limit to friendly copy", async () => {
    postMock.mockImplementation((path: string) => {
      if (path === "/auth/login") return Promise.resolve(need2fa);
      if (path === "/auth/2fa/email/send") return Promise.reject(new ApiError(409, "rate", "rate_limited"));
      return Promise.resolve({});
    });
    renderLogin();
    await signIn();
    fireEvent.click(await screen.findByRole("tab", { name: "Email codes" }));
    fireEvent.click(screen.getByRole("button", { name: "Send code" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many codes requested");
    expect(postMock).toHaveBeenCalledWith("/auth/2fa/email/send", { pre_auth_token: "tok1" });
  });

  it("uses a backup code via the link and verifies on submit", async () => {
    postMock.mockImplementation((path: string) => Promise.resolve(path === "/auth/login" ? need2fa : { id: 1 }));
    renderLogin();
    await signIn();
    fireEvent.click(await screen.findByText("Use a backup code"));
    fireEvent.change(screen.getByLabelText("Backup code"), { target: { value: "ABCD2345" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/auth/2fa/verify", { pre_auth_token: "tok1", method: "backup", code: "ABCD2345" }),
    );
  });

  it("returns to credentials with a message on 410", async () => {
    postMock.mockImplementation((path: string) =>
      path === "/auth/login" ? Promise.resolve(need2fa) : Promise.reject(new ApiError(410, "gone", "pre_auth_expired")),
    );
    renderLogin();
    await signIn();
    fireEvent.change(await screen.findByLabelText("Code"), { target: { value: "123456" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("verification expired");
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
  });

  it("shows an invalid-code error on 401 and stays on the step", async () => {
    postMock.mockImplementation((path: string) =>
      path === "/auth/login" ? Promise.resolve(need2fa) : Promise.reject(new ApiError(401, "bad", "invalid_code")),
    );
    renderLogin();
    await signIn();
    fireEvent.change(await screen.findByLabelText("Code"), { target: { value: "000000" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("isn't right");
  });

  it("OIDC ?step=2fa uses the redirect token and honours return_to", async () => {
    postMock.mockResolvedValue({ id: 1, return_to: "/library" });
    const router = renderLogin("/login?step=2fa&token=oidc-tok&methods=totp,backup");
    fireEvent.change(await screen.findByLabelText("Code"), { target: { value: "654321" } });
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/auth/2fa/verify", { pre_auth_token: "oidc-tok", method: "totp", code: "654321" }),
    );
    await waitFor(() => expect(router.state.location.pathname).toBe("/library"));
    expect(router.state.location.search).not.toHaveProperty("token");
  });

  it("OIDC ?step=2fa without usable methods shows the expired notice", async () => {
    renderLogin("/login?step=2fa&token=oidc-tok&methods=bogus");
    expect(await screen.findByRole("alert")).toHaveTextContent("verification expired");
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
  });

  it("OIDC fragment #step=2fa uses the token and strips the fragment", async () => {
    postMock.mockResolvedValue({ id: 1 });
    window.location.hash = "#step=2fa&token=frag-tok&methods=totp,backup";
    renderLogin();
    fireEvent.change(await screen.findByLabelText("Code"), { target: { value: "111222" } });
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/auth/2fa/verify", { pre_auth_token: "frag-tok", method: "totp", code: "111222" }),
    );
    expect(window.location.hash).toBe("");
  });
});
