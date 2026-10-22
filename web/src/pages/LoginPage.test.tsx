import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

import { LoginPage } from "@/pages/LoginPage";

function renderLoginPage(entry = "/login") {
  const rootRoute = createRootRoute();
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/login",
    component: LoginPage,
    validateSearch: (s: Record<string, unknown>) => ({
      error: typeof s.error === "string" ? s.error : undefined,
    }),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([loginRoute]),
    history: createMemoryHistory({ initialEntries: [entry] }),
  });
  const queryClient = new QueryClient();

  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue([{ slug: "authentik", name: "Authentik" }]);
  });

  it("renders a provider button linking to start", async () => {
    renderLoginPage();
    expect(await screen.findByRole("link", { name: /sign in with authentik/i })).toHaveAttribute(
      "href",
      "/api/auth/oidc/authentik/start",
    );
  });

  it("shows the error banner from ?error=", async () => {
    renderLoginPage("/login?error=not_provisioned");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your account isn't set up here yet. Ask an admin to add you.",
    );
  });

  it("uses generic copy for unknown error codes", async () => {
    renderLoginPage("/login?error=weird");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Single sign-on failed. Try again or use your password.",
    );
  });

  it("renders the username and password fields and a submit button", async () => {
    renderLoginPage();

    expect(await screen.findByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });
});
