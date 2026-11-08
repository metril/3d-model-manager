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

const FILE = { id: 5, rel_path: "parts/case.3mf" };

function host() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <SliceDialog open onOpenChange={() => {}} file={FILE} />
    </QueryClientProvider>
  );
}

function mockApi(embedded: unknown) {
  getMock.mockImplementation((path: string) => {
    if (path === "/printers") return Promise.resolve([]);
    if (path.startsWith("/recipes")) {
      return Promise.resolve([
        { id: 3, name: "Draft", process_profile_name: "0.28mm Extra Draft", process_layer_height_mm: 0.28 },
        { id: 4, name: "Fine", process_profile_name: "0.20mm Standard", process_layer_height_mm: 0.2 },
      ]);
    }
    if (path === "/slicer/health") return Promise.resolve({ configured: true, ok: true });
    if (path === "/files/5") return Promise.resolve({ format: "3mf", embedded });
    return Promise.resolve(null);
  });
  postMock.mockResolvedValue({ id: "job-1" });
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
});

describe("SliceDialog recipe process info", () => {
  it("shows each recipe's process + layer height, flags a mismatch with the file, and notes the replacement", async () => {
    mockApi({ layer_height: 0.2, print_settings_id: "0.20mm Standard @BBL X1C", printer_settings_id: "P1S" });
    render(host());
    const draft = await screen.findByTestId("recipe-process-3");
    expect(draft).toHaveTextContent("Process: 0.28mm Extra Draft · 0.28 mm");
    expect(draft).toHaveTextContent("file uses 0.2 mm");
    expect(screen.getByTestId("recipe-process-4")).not.toHaveTextContent("file uses");
    expect(await screen.findByText(/replace the ones saved in this file/)).toBeInTheDocument();
    expect(screen.getByText(/0\.20mm Standard @BBL X1C/)).toBeInTheDocument();
  });

  it("slices with the file's own settings when the option is ticked", async () => {
    mockApi({ layer_height: 0.2, print_settings_id: "x", printer_settings_id: null });
    render(host());
    fireEvent.click(await screen.findByLabelText(/Draft/));
    fireEvent.click(await screen.findByLabelText(/Keep this file's own process settings/));
    fireEvent.click(screen.getByRole("button", { name: /^slice$/i }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());
    expect(postMock.mock.calls[0][0]).toBe("/files/5/slice");
    expect(postMock.mock.calls[0][1]).toMatchObject({ recipe_id: 3, file_settings: true });
  });

  it("hides the option for a file with no embedded settings", async () => {
    mockApi(null);
    render(host());
    await screen.findByTestId("recipe-process-3");
    expect(screen.queryByTestId("file-settings-option")).not.toBeInTheDocument();
  });
});
