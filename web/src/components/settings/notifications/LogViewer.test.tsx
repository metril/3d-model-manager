import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LogViewer } from "@/components/settings/notifications/LogViewer";

import { renderWithQuery } from "./testUtils";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

const item = (id: number) => ({
  id,
  channel_id: 1,
  channel_name: "Phone",
  event: "print_done",
  printer_id: null,
  status: "failed",
  error: "timeout",
  sent_at: "2026-10-23T00:00:00Z",
});

describe("LogViewer", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue({ items: [item(1)], total: 120 });
  });

  it("renders rows and pages with limit/offset", async () => {
    renderWithQuery(<LogViewer />);
    expect(await screen.findByText("timeout")).toBeInTheDocument();
    expect(screen.getByText(/1–50 of 120/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/notifications/log?limit=50&offset=50"));
  });

  it("filters by status and resets the offset", async () => {
    renderWithQuery(<LogViewer />);
    await screen.findByText("timeout");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "failed" } });
    await waitFor(() =>
      expect(getMock).toHaveBeenCalledWith("/notifications/log?limit=50&offset=0&status=failed"),
    );
  });
});
