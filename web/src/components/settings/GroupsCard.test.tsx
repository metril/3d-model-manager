import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GroupsCard } from "@/components/settings/GroupsCard";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock } };
});

const group = { id: 5, name: "Staff", description: null, permissions: [], members: [], printer_access: [] };

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <GroupsCard />
    </QueryClientProvider>,
  );
}

describe("GroupsCard", () => {
  beforeEach(() => {
    getMock.mockReset().mockImplementation((path: string) => {
      if (path === "/admin/groups") return Promise.resolve([group]);
      if (path === "/printers") return Promise.resolve([{ id: 1, name: "A1 Mini" }]);
      return Promise.resolve([]);
    });
    putMock.mockReset().mockResolvedValue(group);
  });

  it("saves the printer access matrix as one PUT", async () => {
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Edit Staff" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Print on A1 Mini" }));
    fireEvent.click(screen.getByRole("button", { name: "Save printer access" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/admin/groups/5/printer-access", [
        { printer_id: 1, can_print: true, can_control: false, requires_review: false },
      ]),
    );
  });

  it("omits all-false rows", async () => {
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Edit Staff" }));
    await screen.findByRole("checkbox", { name: "Print on A1 Mini" });
    fireEvent.click(screen.getByRole("button", { name: "Save printer access" }));
    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/admin/groups/5/printer-access", []));
  });
});
