import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SliceDialog } from "@/components/slice/SliceDialog";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});
vi.mock("sonner", () => ({ toast: { custom: vi.fn(), dismiss: vi.fn(), error: vi.fn(), success: vi.fn() } }));
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ can: () => true }),
}));

const FILE = { id: 5, rel_path: "parts/cube.stl" };

function host(open: boolean) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <SliceDialog open={open} onOpenChange={() => {}} file={FILE} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockImplementation((path: string) => {
    if (path === "/printers") return Promise.resolve([{ id: 1, name: "Printer One" }, { id: 2, name: "Printer Two" }]);
    if (path.startsWith("/recipes")) {
      const two = path.includes("printer_id=2");
      return Promise.resolve([
        { id: 3, name: "A1 PLA", compatible: true },
        { id: 4, name: "A1 PETG", compatible: !two },
      ]);
    }
    if (path === "/slicer/health") return Promise.resolve({ configured: true, ok: true });
    return Promise.resolve(null);
  });
});

describe("SliceDialog lifecycle", () => {
  it("mounts nothing (and queries nothing) while closed", () => {
    render(host(false));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(getMock).not.toHaveBeenCalled();
  });

  it("resets the recipe choice when closed and reopened", async () => {
    const { rerender } = render(host(true));
    fireEvent.click(await screen.findByLabelText("A1 PETG"));
    await waitFor(() => expect(screen.getByLabelText("A1 PETG")).toBeChecked());
    rerender(host(false));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    rerender(host(true));
    expect(await screen.findByLabelText("A1 PETG")).not.toBeChecked();
  });

  it("clears a recipe that turns incompatible after switching printer", async () => {
    render(host(true));
    fireEvent.click(await screen.findByLabelText("Printer One"));
    fireEvent.click(await screen.findByLabelText("A1 PETG"));
    await waitFor(() => expect(screen.getByLabelText("A1 PETG")).toBeChecked());
    fireEvent.click(screen.getByLabelText("Printer Two"));
    await waitFor(() => expect(screen.getByLabelText("A1 PETG")).not.toBeChecked());
    await waitFor(() => expect(screen.getByLabelText("A1 PLA")).toBeChecked());
  });
});
