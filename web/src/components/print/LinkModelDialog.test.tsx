import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LinkModelDialog } from "@/components/print/LinkModelDialog";

const { patchMock } = vi.hoisted(() => ({ patchMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, patch: patchMock } };
});
vi.mock("@/api/library", () => ({
  useModelSearchQuery: (q: string) => ({
    data: q.trim() ? { items: [{ id: 5, name: "Benchy" }] } : undefined,
    isFetching: false,
  }),
}));

describe("LinkModelDialog", () => {
  beforeEach(() => patchMock.mockReset().mockResolvedValue({}));

  it("searches and PATCHes the archive with the picked model", async () => {
    const onOpenChange = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <LinkModelDialog archiveId={9} open onOpenChange={onOpenChange} />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText("Search models"), { target: { value: "benchy" } });
    fireEvent.click(await screen.findByText("Benchy"));
    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("/archives/9", { model_id: 5, revision_id: null }),
    );
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });
});
