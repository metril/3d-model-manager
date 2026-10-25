import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EmailPrefsCard } from "@/components/settings/EmailPrefsCard";

import { renderWithQuery } from "./notifications/testUtils";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock } };
});

const base = {
  notify_print_start: false,
  notify_print_complete: true,
  notify_print_failed: false,
  notify_print_stopped: false,
  smtp_configured: true,
  has_email: true,
};

describe("EmailPrefsCard", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
  });

  it("renders nothing without SMTP", async () => {
    getMock.mockResolvedValue({ ...base, smtp_configured: false });
    const { container } = renderWithQuery(<EmailPrefsCard />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("toggles a preference via PUT", async () => {
    getMock.mockResolvedValue(base);
    putMock.mockResolvedValue({ ...base, notify_print_failed: true });
    renderWithQuery(<EmailPrefsCard />);
    expect(await screen.findByLabelText("Print finished")).toBeChecked();
    fireEvent.click(screen.getByLabelText("Print failed"));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/user-notifications/preferences", {
        notify_print_start: false,
        notify_print_complete: true,
        notify_print_failed: true,
        notify_print_stopped: false,
      }),
    );
    await waitFor(() => expect(screen.getByLabelText("Print failed")).toBeChecked());
  });

  it("hints when the account has no email", async () => {
    getMock.mockResolvedValue({ ...base, has_email: false });
    renderWithQuery(<EmailPrefsCard />);
    expect(await screen.findByText(/no email address/)).toBeInTheDocument();
  });
});
