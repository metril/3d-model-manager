import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { arch, renderQ } from "@/components/print/archiveTestUtils";
import { PostPrintConfirmDialog } from "@/components/print/PostPrintConfirmDialog";

const m = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/api/client", async (orig) => ({
  ...(await orig<typeof import("@/api/client")>()),
  api: { get: m.get, post: m.post },
}));
vi.mock("@/api/auth", () => ({ useAuth: () => ({ data: { id: 1 } }) }));
vi.mock("@/api/features", () => ({ useFeatures: () => ({ data: { printer_enabled: true } }) }));

beforeEach(() => {
  sessionStorage.clear();
  m.get.mockReset();
  m.post.mockReset().mockResolvedValue({});
});

describe("PostPrintConfirmDialog", () => {
  it("shows only the current user's pending archive and posts a dialog verdict", async () => {
    m.get.mockResolvedValue([
      arch({ id: 1, created_by_user_id: 2, confirm_requested: true, model_name: "Other" }),
      arch({ id: 2, created_by_user_id: 1, confirm_requested: true, user_verdict: "good", model_name: "Done" }),
      arch({ id: 3, created_by_user_id: 1, confirm_requested: true, model_name: "Mine" }),
    ]);
    renderQ(<PostPrintConfirmDialog />);
    expect(await screen.findByText(/Mine on A1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Good" }));
    await waitFor(() => expect(m.post).toHaveBeenCalledWith("/archives/3/verdict", { verdict: "good", source: "dialog" }));
  });

  it("renders nothing when only others' archives are pending", async () => {
    m.get.mockResolvedValue([arch({ id: 1, created_by_user_id: 2, confirm_requested: true })]);
    renderQ(<PostPrintConfirmDialog />);
    await waitFor(() => expect(m.get).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("snoozes per archive in sessionStorage", async () => {
    m.get.mockResolvedValue([arch({ id: 3, created_by_user_id: 1, confirm_requested: true })]);
    renderQ(<PostPrintConfirmDialog />);
    fireEvent.click(await screen.findByRole("button", { name: "Later" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(JSON.parse(sessionStorage.getItem("archive-confirm-snooze")!)).toEqual([3]);
  });
});
