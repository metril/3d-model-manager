import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationsCard } from "@/components/settings/notifications/NotificationsCard";

import { catalog, fakeChannel, renderWithQuery } from "./testUtils";

const { getMock, postMock, patchMock, deleteMock, toastMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  patchMock: vi.fn(),
  deleteMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, patch: patchMock, delete: deleteMock } };
});

describe("NotificationsCard", () => {
  beforeEach(() => {
    for (const m of [getMock, postMock, patchMock, deleteMock]) m.mockReset();
    getMock.mockImplementation((p: string) =>
      Promise.resolve(
        p === "/notifications/events"
          ? catalog
          : p === "/notifications/channels"
            ? [fakeChannel()]
            : p === "/notifications/settings"
              ? { hms_renotify_clear_s: 300, hms_renotify_after_s: 3600 }
              : {},
      ),
    );
  });

  it("lists channels and toggles enabled via PATCH", async () => {
    patchMock.mockResolvedValue(fakeChannel({ enabled: false }));
    renderWithQuery(<NotificationsCard />);
    expect(await screen.findByText("Phone")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Enable Phone" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("/notifications/channels/1", { enabled: false }));
  });

  it("saves the HMS re-notify intervals", async () => {
    patchMock.mockResolvedValue({ hms_renotify_clear_s: 60, hms_renotify_after_s: 3600 });
    renderWithQuery(<NotificationsCard />);
    fireEvent.change(await screen.findByLabelText("Re-notify after clear (s)"), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("/notifications/settings", {
        hms_renotify_clear_s: 60,
        hms_renotify_after_s: 3600,
      }),
    );
  });

  it("test-send shows a toast with the result", async () => {
    postMock.mockResolvedValue({ ok: false, error: "boom" });
    renderWithQuery(<NotificationsCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Test Phone" }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("boom"));
  });

  it("deletes after a confirming click", async () => {
    deleteMock.mockResolvedValue(undefined);
    renderWithQuery(<NotificationsCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete Phone" }));
    expect(deleteMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/notifications/channels/1"));
  });
});
