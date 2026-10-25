import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TrashList } from "@/api/types";
import { TrashPage } from "@/pages/TrashPage";

const { getMock, postMock, toastSuccessMock, authMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  toastSuccessMock: vi.fn(),
  authMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => authMock() }));
vi.mock("sonner", () => ({ toast: { success: toastSuccessMock, error: vi.fn() } }));

const item = (id: number, owner: number | null) => ({
  id,
  slug: `m${id}`,
  name: `Model ${id}`,
  created_by_user_id: owner,
  deleted_at: "2026-10-20T10:00:00Z",
  deleted_by: "admin",
  purge_at: "2026-11-19T10:00:00Z",
  file_count: 2,
  size_bytes: 2048,
});

function setup(list: TrashList, me: { id: number }, admin: boolean) {
  getMock.mockResolvedValue(list);
  postMock.mockResolvedValue({ count: 1 });
  authMock.mockReturnValue({ data: me, can: (p: string) => (p === "admin" ? admin : true) });
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/", component: TrashPage });
  const lib = createRoute({ getParentRoute: () => root, path: "/library", component: () => null });
  const router = createRouter({
    routeTree: root.addChildren([route, lib]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  toastSuccessMock.mockReset();
});

describe("TrashPage", () => {
  it("shows the empty state", async () => {
    setup({ items: [], retention_days: 30 }, { id: 1 }, true);
    expect(await screen.findByText("The trash is empty.")).toBeInTheDocument();
  });

  it("restores one item", async () => {
    setup({ items: [item(1, 1)], retention_days: 30 }, { id: 1 }, false);
    fireEvent.click((await screen.findAllByRole("button", { name: /^Restore$/ })).at(-1)!);
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/trash/restore", { ids: [1] }));
    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalledWith("Restored 1 model"));
  });

  it("empty trash is count-confirmed and only counts items the user may purge", async () => {
    setup({ items: [item(1, 1), item(2, 1), item(3, 99)], retention_days: 0 }, { id: 1 }, false);
    fireEvent.click(await screen.findByRole("button", { name: "Empty trash" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Permanently delete 2 models?")).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Empty trash" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/trash/purge", { ids: [1, 2] }));
  });

  it("purges only the selected items", async () => {
    setup({ items: [item(1, 1), item(2, 1)], retention_days: 30 }, { id: 1 }, true);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Model 2" }));
    fireEvent.click(screen.getByRole("button", { name: /Delete permanently \(1\)/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Permanently delete 1 model?")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete permanently" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/trash/purge", { ids: [2] }));
  });
});
