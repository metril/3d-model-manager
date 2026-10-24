import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderQ } from "@/components/print/archiveTestUtils";
import { ArchivePurgeCard } from "@/components/settings/ArchivePurgeCard";

const m = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("@/api/client", async (orig) => ({
  ...(await orig<typeof import("@/api/client")>()),
  api: { get: m.get, post: m.post, put: m.put },
}));
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => true }) }));

beforeEach(() => {
  m.get.mockReset().mockImplementation(async (p: string) =>
    p.includes("stats")
      ? { last_run_at: "2026-10-23T03:30:00Z", last_deleted: 4, last_bytes: 2048, next_run: "03:30 daily" }
      : { enabled: true, older_than_days: 90, keep_failed: false, keep_with_verdict: true },
  );
  m.post.mockReset().mockImplementation(async (p: string) =>
    p.endsWith("preview") ? { count: 7, bytes: 5 * 1024 * 1024 } : { deleted: 7, bytes: 5 * 1024 * 1024 },
  );
  m.put.mockReset().mockResolvedValue({});
});

describe("ArchivePurgeCard", () => {
  it("saves settings, previews, then runs after confirm", async () => {
    renderQ(<ArchivePurgeCard />);
    expect(await screen.findByTestId("purge-stats")).toHaveTextContent("4 deleted");
    fireEvent.change(screen.getByLabelText("Older than (days)"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(m.put).toHaveBeenCalledWith("/settings/archive_purge", {
        enabled: true, older_than_days: 30, keep_failed: false, keep_with_verdict: true,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByTestId("purge-preview")).toHaveTextContent("7 runs");
    expect(m.post).not.toHaveBeenCalledWith("/archives/purge/run");
    fireEvent.click(screen.getByRole("button", { name: "Purge now" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Delete 7 archived runs?");
    fireEvent.click(Array.from(dialog.querySelectorAll("button")).find((b) => b.textContent === "Purge now")!);
    await waitFor(() => expect(m.post).toHaveBeenCalledWith("/archives/purge/run"));
  });
});
