import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TwoFactorStatus } from "@/api/auth";
import { ApiError } from "@/api/client";
import { TwoFactorCard } from "@/components/settings/TwoFactorCard";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

const off: TwoFactorStatus = { totp_enabled: false, email_otp_enabled: false, backup_codes_remaining: 0, methods: [], smtp_configured: true, email_available: true };
const on: TwoFactorStatus = { ...off, totp_enabled: true, backup_codes_remaining: 7, methods: ["totp", "backup"] };
let status: TwoFactorStatus = off;

function renderCard() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TwoFactorCard />
    </QueryClientProvider>,
  );
}

describe("TwoFactorCard", () => {
  beforeEach(() => {
    status = off;
    getMock.mockReset().mockImplementation(() => Promise.resolve(status));
    postMock.mockReset();
  });

  it("sets up TOTP: QR + secret, enable, then shows one-time backup codes", async () => {
    postMock.mockImplementation((path: string) => {
      if (path === "/auth/2fa/totp/setup")
        return Promise.resolve({ secret: "JBSWY3DP", otpauth_url: "otpauth://totp/x:alice?secret=JBSWY3DP&issuer=x", setup_token: "st" });
      if (path === "/auth/2fa/totp/enable") {
        status = on;
        return Promise.resolve({ backup_codes: ["AAAA1111", "BBBB2222"] });
      }
      return Promise.resolve(undefined);
    });
    renderCard();
    fireEvent.click(await screen.findAllByRole("button", { name: "Set up" }).then((b) => b[0]));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByTestId("totp-secret")).toHaveTextContent("JBSWY3DP");
    expect(within(dialog).getByRole("img", { name: "Authenticator QR code" })).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("6-digit code"), { target: { value: "123456" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Verify and enable" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/auth/2fa/totp/enable", { setup_token: "st", code: "123456" }),
    );
    expect(await within(dialog).findByText("AAAA1111")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Download .txt" })).toBeInTheDocument();
  });

  it("shows an error for a wrong enable code", async () => {
    postMock.mockImplementation((path: string) =>
      path === "/auth/2fa/totp/setup"
        ? Promise.resolve({ secret: "S", otpauth_url: "otpauth://totp/x?secret=S", setup_token: "st" })
        : Promise.reject(new ApiError(400, "bad", "invalid_code")),
    );
    renderCard();
    fireEvent.click((await screen.findAllByRole("button", { name: "Set up" }))[0]);
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(await within(dialog).findByLabelText("6-digit code"), { target: { value: "000000" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Verify and enable" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("isn't right");
  });

  it("disables TOTP with a password/code", async () => {
    status = on;
    postMock.mockResolvedValue(undefined);
    renderCard();
    expect(await screen.findByText("Codes remaining: 7")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Disable" })[0]);
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Password or current code"), { target: { value: "hunter2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Disable" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/auth/2fa/totp/disable", { code_or_password: "hunter2" }));
  });

  it("regenerates backup codes", async () => {
    status = on;
    postMock.mockResolvedValue({ backup_codes: ["NEWC0DE1"] });
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Regenerate" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("6-digit code"), { target: { value: "111222" } });
    fireEvent.click(within(dialog).getAllByRole("button", { name: "Regenerate" })[0]);
    expect(await screen.findByText("NEWC0DE1")).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/auth/2fa/backup/regenerate", { code: "111222" });
  });

  it("enables email codes: request, then confirm the emailed code", async () => {
    postMock.mockResolvedValue({ sent: true });
    renderCard();
    const setUp = await screen.findAllByRole("button", { name: "Set up" });
    fireEvent.click(setUp[1]);
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/auth/2fa/email/enable"));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Emailed code"), { target: { value: "998877" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Enable" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/auth/2fa/email/confirm", { code: "998877" }));
  });

  it("disables the email row when SMTP isn't configured", async () => {
    status = { ...off, smtp_configured: false, email_available: false };
    renderCard();
    expect(await screen.findByText(/configure outgoing email/)).toBeInTheDocument();
    expect((await screen.findAllByRole("button", { name: "Set up" }))[1]).toBeDisabled();
  });
});
