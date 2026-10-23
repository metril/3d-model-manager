import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BackupsCard } from "@/components/settings/BackupsCard";
import { UpdateBadge } from "@/components/shell/UpdateBadge";
import { UpdatesCard } from "@/components/settings/UpdatesCard";

const { getMock, postMock, putMock, adminBox } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  adminBox: { current: true },
}));
vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ can: () => adminBox.current }),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock } };
});

const OPS = {
  backup_enabled: true,
  backup_interval_hours: 24,
  backup_retention: 7,
  git_provider: "github",
  git_base_url: null,
  git_repo: "me/backups",
  git_branch: "main",
  git_token_set: true,
  update_check_enabled: true,
};
const RUNS = [
  { id: 5, kind: "manual", status: "ok", size_bytes: 2_097_152, location: "local:backups/5.dump", git_pushed: true,
    error: null, created_by_user_id: 1, created_at: "2026-10-23T10:00:00Z", finished_at: null },
  { id: 4, kind: "scheduled", status: "failed", size_bytes: null, location: null, git_pushed: false,
    error: "disk full", created_by_user_id: null, created_at: "2026-10-22T10:00:00Z", finished_at: null },
];
const UPDATE = { latest: "v0.5.0", current: "0.4.0", url: "https://example.test/rel", notes: null,
  update_available: true, checked_at: null, error: null };

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  putMock.mockReset();
  adminBox.current = true;
  getMock.mockImplementation((p: string) =>
    Promise.resolve(p === "/settings/ops" ? OPS : p === "/admin/backups" ? RUNS : UPDATE),
  );
});

describe("BackupsCard", () => {
  it("lists runs, runs a backup, saves settings without echoing the token", async () => {
    postMock.mockResolvedValue(RUNS[0]);
    putMock.mockResolvedValue(OPS);
    wrap(<BackupsCard />);
    expect(await screen.findByText(/2\.0 MB/)).toBeInTheDocument();
    expect(screen.getByText("disk full")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Restore settings" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Run backup now" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/admin/backups/run"));
    await screen.findByDisplayValue("me/backups");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0][1]).not.toHaveProperty("git_token");
    expect(putMock.mock.calls[0][1]).toMatchObject({ git_provider: "github", backup_interval_hours: 24 });
  });

  it("confirms before restoring settings", async () => {
    postMock.mockResolvedValue({ restored: 12 });
    wrap(<BackupsCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Restore settings" }));
    expect(postMock).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/admin/backups/5/restore-settings"));
  });

  it("hides run list and run button for non-admins", async () => {
    adminBox.current = false;
    wrap(<BackupsCard />);
    await screen.findByText("Backups");
    expect(screen.queryByRole("button", { name: "Run backup now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restore settings" })).toBeNull();
  });
});

describe("UpdatesCard / UpdateBadge", () => {
  it("shows current vs latest, notes link and support bundle", async () => {
    wrap(<UpdatesCard />);
    expect(await screen.findByText("v0.5.0")).toBeInTheDocument();
    expect(screen.getByText("0.4.0")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Release notes" })).toHaveAttribute("href", "https://example.test/rel");
    expect(screen.getByRole("link", { name: "Support bundle" })).toHaveAttribute("href", "/api/admin/support-bundle");
  });

  it("badge appears only when update_available", async () => {
    const { unmount } = wrap(<UpdateBadge />);
    expect(await screen.findByText("Update v0.5.0")).toBeInTheDocument();
    unmount();
    getMock.mockResolvedValue({ ...UPDATE, update_available: false });
    const { container } = wrap(<UpdateBadge />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
