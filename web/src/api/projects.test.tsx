import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { useDeleteProject } from "@/api/projects";

const { deleteMock } = vi.hoisted(() => ({ deleteMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, delete: deleteMock } };
});

describe("useDeleteProject", () => {
  it("removes the project's detail/timeline/archives queries instead of refetching them", async () => {
    deleteMock.mockResolvedValue(undefined);
    const qc = new QueryClient();
    qc.setQueryData(["projects", "detail", 7], { id: 7 });
    qc.setQueryData(["projects", "timeline", 7], []);
    qc.setQueryData(["projects", "archives", 7], []);
    qc.setQueryData(["projects", "detail", 8], { id: 8 });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useDeleteProject(), { wrapper });
    await act(async () => {
      result.current.mutate(7);
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(qc.getQueryData(["projects", "detail", 7])).toBeUndefined();
    expect(qc.getQueryData(["projects", "timeline", 7])).toBeUndefined();
    expect(qc.getQueryData(["projects", "archives", 7])).toBeUndefined();
    expect(qc.getQueryData(["projects", "detail", 8])).toBeDefined();
  });
});
