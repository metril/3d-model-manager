import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AddToProjectDialog } from "@/components/projects/AddToProjectDialog";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("AddToProjectDialog", () => {
  beforeEach(() => {
    getMock.mockReset().mockResolvedValue([]);
    postMock.mockReset();
  });

  it("retries the add against the project it already created, not a duplicate", async () => {
    postMock.mockImplementation((path: string) => {
      if (path === "/projects") return Promise.resolve({ id: 11 });
      if (postMock.mock.calls.filter((c) => String(c[0]).includes("/11")).length === 1)
        return Promise.reject(new Error("boom"));
      return Promise.resolve({});
    });
    const onOpenChange = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AddToProjectDialog open onOpenChange={onOpenChange} modelIds={[1, 2]} />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "new" } });
    fireEvent.change(screen.getByLabelText("New project name"), { target: { value: "Bench" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(postMock.mock.calls.filter((c) => c[0] === "/projects")).toHaveLength(1));
    await waitFor(() => expect(postMock.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(postMock.mock.calls.filter((c) => c[0] === "/projects")).toHaveLength(1);
  });
});
