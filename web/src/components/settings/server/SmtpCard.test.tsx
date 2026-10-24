import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { serverSettingsQueryOptions } from "@/api/serverSettings";
import { fakeServer } from "@/components/settings/server/fixtures";
import { SmtpCard } from "@/components/settings/server/SmtpCard";
import { TooltipProvider } from "@/components/ui/tooltip";

const { postMock, putMock } = vi.hoisted(() => ({ postMock: vi.fn(), putMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, put: putMock } };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const data = fakeServer({
  smtp: { host: "smtp.example.com", port: 587, security: "starttls", username: "mailer", password: "***", from_addr: "tdmm@example.com", from_name: "TDMM" },
});

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(serverSettingsQueryOptions.queryKey, data);
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <SmtpCard data={data} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("SmtpCard", () => {
  beforeEach(() => {
    postMock.mockReset();
    putMock.mockReset().mockImplementation((_p: string, body: unknown) => Promise.resolve({ ...data, config: body }));
  });

  it("keeps the masked password when untouched", async () => {
    renderCard();
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "mail.example.org" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0][1].smtp).toMatchObject({ host: "mail.example.org", port: 587, password: "***", from_addr: "tdmm@example.com" });
  });

  it("sends a new password and follows the default port on security change", async () => {
    renderCard();
    fireEvent.change(screen.getByLabelText("Security"), { target: { value: "tls" } });
    expect(screen.getByLabelText("Port")).toHaveValue(465);
    const pw = screen.getByLabelText("Password");
    fireEvent.focus(pw);
    fireEvent.change(pw, { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0][1].smtp).toMatchObject({ security: "tls", port: 465, password: "s3cret" });
  });

  it("sends a test email and shows ok / error", async () => {
    postMock.mockResolvedValueOnce({ ok: true, error: null }).mockResolvedValueOnce({ ok: false, error: "auth failed" });
    renderCard();
    fireEvent.change(screen.getByLabelText("Send a test email to"), { target: { value: "me@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send test email" }));
    expect(await screen.findByText("Test email sent.")).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/settings/server/smtp/test", { to: "me@example.com" });
    fireEvent.click(screen.getByRole("button", { name: "Send test email" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Test failed: auth failed");
  });

  it("clears the stored password explicitly", async () => {
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Clear password" }));
    const pw = screen.getByLabelText("Password");
    fireEvent.focus(pw);
    fireEvent.blur(pw);
    expect(pw).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0][1].smtp.password).toBe("");
  });
});
