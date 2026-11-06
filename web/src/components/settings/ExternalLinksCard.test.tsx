import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExternalLinksCard } from "@/components/settings/ExternalLinksCard";

const { getMock, postMock, deleteMock, patchMock } = vi.hoisted(() => ({
  patchMock: vi.fn(),
  getMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, delete: deleteMock, patch: patchMock } };
});

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ExternalLinksCard />
    </QueryClientProvider>,
  );
}

describe("ExternalLinksCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getMock.mockResolvedValue([
      { id: 1, name: "Grafana", url: "https://g.example", icon: null, sort_order: 0, open_mode: "iframe" },
    ]);
  });

  it("lists existing links", async () => {
    renderCard();
    expect(await screen.findByText("Grafana")).toBeInTheDocument();
    expect(screen.getByText(/g\.example · embedded/)).toBeInTheDocument();
  });

  it("creates a link with the next sort order and chosen mode", async () => {
    postMock.mockResolvedValue({});
    renderCard();
    await screen.findByText("Grafana");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "HA" } });
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "https://ha.example" } });
    fireEvent.change(screen.getByLabelText("Open in"), { target: { value: "newtab" } });
    fireEvent.click(screen.getByRole("button", { name: "Add link" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/external-links", {
        name: "HA",
        url: "https://ha.example",
        icon: null,
        open_mode: "newtab",
        sort_order: 1,
      }),
    );
  });

  it("deletes a link", async () => {
    deleteMock.mockResolvedValue(undefined);
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Delete Grafana" }));
    expect(deleteMock).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/external-links/1"));
  });

  const three = [
    { id: 1, name: "A", url: "https://a.example", icon: null, sort_order: 0, open_mode: "iframe" },
    { id: 2, name: "B", url: "https://b.example", icon: null, sort_order: 5, open_mode: "iframe" },
    { id: 3, name: "C", url: "https://c.example", icon: null, sort_order: 5, open_mode: "iframe" },
  ];

  it("renumbers to the new order when moving, even with duplicate sort orders", async () => {
    getMock.mockResolvedValue(three);
    patchMock.mockResolvedValue({});
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Move C up" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalledTimes(2));
    expect(patchMock).toHaveBeenCalledWith("/external-links/3", { sort_order: 1 });
    expect(patchMock).toHaveBeenCalledWith("/external-links/2", { sort_order: 2 });
  });

  it("rolls back applied changes when a move fails", async () => {
    getMock.mockResolvedValue(three);
    patchMock.mockResolvedValueOnce({}).mockRejectedValueOnce(new Error("x")).mockResolvedValue({});
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Move C up" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalledTimes(3));
    expect(patchMock).toHaveBeenLastCalledWith("/external-links/3", { sort_order: 5 });
  });
});
