import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AmsBackupBar } from "./AmsBackupBar";
import { renderCard } from "./testUtils";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});

const SNAP = { taken_at: "2026-10-30T10:00:00Z", slots: [{ ams_id: 0, tray_id: 0, tray_type: "PLA", color: "FF0000" }, { ams_id: 0, tray_id: 1, tray_type: "PETG", color: null }] };

describe("AmsBackupBar", () => {
  beforeEach(() => {
    getMock.mockReset().mockResolvedValue(null);
    postMock.mockReset().mockResolvedValue({});
  });

  it("backs up, and hides restore until a backup exists", async () => {
    postMock.mockResolvedValue(SNAP);
    renderCard(<AmsBackupBar printerId={3} busy={false} />);
    const btn = await screen.findByRole("button", { name: "Back up slots" });
    expect(screen.queryByRole("button", { name: /Restore slots/ })).toBeNull();
    fireEvent.click(btn);
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/3/ams/backup"));
    expect(await screen.findByRole("button", { name: "Restore slots (2)" })).toBeInTheDocument();
  });

  it("restore is gated behind a count confirm", async () => {
    getMock.mockResolvedValue(SNAP);
    postMock.mockResolvedValue({ restored: 2, failed: 0, pending: 0 });
    renderCard(<AmsBackupBar printerId={3} busy={false} />);
    fireEvent.click(await screen.findByRole("button", { name: "Restore slots (2)" }));
    expect(postMock).not.toHaveBeenCalled();
    expect(await screen.findByText(/Rewrites 2 slots/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Restore slots" }).at(-1)!);
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/3/ams/restore"));
  });
});
