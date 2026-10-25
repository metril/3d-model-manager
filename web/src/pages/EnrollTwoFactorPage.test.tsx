import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ENROLL_2FA_EVENT } from "@/api/client";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

import { EnrollTwoFactorPage } from "@/pages/EnrollTwoFactorPage";

let me = { id: 1, username: "a", two_factor_enabled: false, must_enroll_2fa: true };
const status = { totp_enabled: false, email_otp_enabled: false, backup_codes_remaining: 0, methods: [], smtp_configured: false, email_available: false };

function renderPage(onFinished = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <EnrollTwoFactorPage onFinished={onFinished} />
    </QueryClientProvider>,
  );
  return onFinished;
}

describe("EnrollTwoFactorPage", () => {
  beforeEach(() => {
    me = { id: 1, username: "a", two_factor_enabled: false, must_enroll_2fa: true };
    getMock.mockReset().mockImplementation((path: string) => Promise.resolve(path === "/auth/me" ? me : status));
    postMock.mockReset().mockResolvedValue(undefined);
  });

  it("offers only enrolment and sign-out until a method is enabled", async () => {
    renderPage();
    expect(await screen.findByText("Set up two-factor authentication")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log out" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  });

  it("shows Continue once two-factor is enabled", async () => {
    me = { ...me, two_factor_enabled: true };
    const done = renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
    expect(done).toHaveBeenCalled();
  });

  it("exports the enrolment event name the client fires on 403", () => {
    expect(ENROLL_2FA_EVENT).toBe("tdmm:2fa-enrollment-required");
  });
});
