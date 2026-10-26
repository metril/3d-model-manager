import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CloudPresetsDialog } from "@/components/inventory/CloudPresetsDialog";
import { PipelineForm } from "@/components/inventory/PipelineForm";

const { getMock, postMock, patchMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  patchMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock, patch: patchMock, delete: deleteMock },
  };
});

vi.mock("@/components/ui/select", () => import("@/test/selectMock"));
vi.mock("@/components/ui/checkbox", () => ({
  Checkbox: ({
    checked,
    onCheckedChange,
    ...rest
  }: { checked?: boolean; onCheckedChange: (checked: boolean) => void } & Record<string, unknown>) => (
    <input type="checkbox" checked={checked} onChange={(e) => onCheckedChange(e.target.checked)} {...rest} />
  ),
}));
vi.mock("@/components/ui/tabs", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  const Ctx = React.createContext<{ value: string; set: (v: string) => void }>({ value: "", set: () => {} });
  return {
    Tabs: ({ value, onValueChange, children }: { value: string; onValueChange: (v: string) => void; children?: ReactNode }) => (
      <Ctx.Provider value={{ value, set: onValueChange }}>{children}</Ctx.Provider>
    ),
    TabsList: ({ children }: { children?: ReactNode }) => <div role="tablist">{children}</div>,
    TabsTrigger: ({ value, children }: { value: string; children?: ReactNode }) => {
      const ctx = React.useContext(Ctx);
      return (
        <button role="tab" type="button" aria-selected={ctx.value === value} onClick={() => ctx.set(value)}>
          {children}
        </button>
      );
    },
    TabsContent: ({ value, children }: { value: string; children?: ReactNode }) => {
      const ctx = React.useContext(Ctx);
      return ctx.value === value ? <div>{children}</div> : null;
    },
  };
});

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  [getMock, postMock, patchMock, deleteMock].forEach((m) => m.mockReset());
});

describe("PipelineForm", () => {
  beforeEach(() => {
    getMock.mockImplementation((path: string) => {
      if (path === "/recipes") return Promise.resolve([{ id: 3, name: "A1 PLA" }]);
      if (path === "/printers") return Promise.resolve([{ id: 9, name: "Mini" }]);
      return Promise.resolve(null);
    });
  });

  it("creates a pipeline with pre-steps and a queue step", async () => {
    postMock.mockResolvedValue({ id: 1 });
    const onOpenChange = vi.fn();
    wrap(<PipelineForm open onOpenChange={onOpenChange} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Quick print" } });
    await screen.findByRole("option", { name: "A1 PLA" });
    fireEvent.change(screen.getByLabelText("Recipe"), { target: { value: "3" } });
    fireEvent.click(screen.getByLabelText("Auto-orient the model"));
    fireEvent.change(screen.getByLabelText("After slicing"), { target: { value: "queue" } });
    fireEvent.change(await screen.findByLabelText("Copies"), { target: { value: "2" } });
    await screen.findByRole("option", { name: "Mini" });
    fireEvent.change(screen.getByLabelText("Printer"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Create pipeline" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/slicer-pipelines", {
        name: "Quick print",
        description: null,
        recipe_id: 3,
        plate: null,
        steps: [
          { kind: "orient" },
          { kind: "queue", printer_id: 9, copies: 2, front: false, manual_start: false },
        ],
      }),
    );
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("blocks a rename template with a path separator and requires a recipe", async () => {
    wrap(<PipelineForm open onOpenChange={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "P" } });
    const submit = screen.getByRole("button", { name: "Create pipeline" });
    expect(submit).toBeDisabled(); // no recipe yet
    await screen.findByRole("option", { name: "A1 PLA" });
    fireEvent.change(screen.getByLabelText("Recipe"), { target: { value: "3" } });
    expect(submit).toBeEnabled();
    fireEvent.change(screen.getByLabelText("After slicing"), { target: { value: "rename" } });
    fireEvent.change(await screen.findByLabelText("File name template"), { target: { value: "a/b" } });
    expect(submit).toBeDisabled();
  });
});

describe("CloudPresetsDialog", () => {
  const preset = (over: Record<string, unknown>) => ({
    preset_id: "a",
    name: "My PLA",
    kind: "filament",
    updated_time: null,
    status: "new",
    profile_id: null,
    ...over,
  });

  function mockCloud(status: Record<string, unknown>, presets = [preset({}), preset({ preset_id: "b", name: "Quality", kind: "process", status: "remote_changed", profile_id: 4 })]) {
    getMock.mockImplementation((path: string) => {
      if (path === "/cloud-presets/status")
        return Promise.resolve({
          orca: { connected: false, account: null, needs_reconnect: false, pending: false },
          bambu: { connected: false, account: null, needs_reconnect: false, pending: false },
          ...status,
        });
      if (path === "/cloud-presets/orca/presets") return Promise.resolve({ presets, removed: [] });
      if (path === "/cloud-presets/orca/presets/b/diff")
        return Promise.resolve({
          preset_id: "b",
          name: "Quality",
          status: "remote_changed",
          profile_id: 4,
          changes: [{ key: "layer_height", local: "0.2", remote: "0.16" }],
        });
      return Promise.resolve(null);
    });
  }

  it("lists presets with status, shows a diff and imports one", async () => {
    mockCloud({ orca: { connected: true, account: null, needs_reconnect: false, pending: false } });
    postMock.mockResolvedValue({ created: 0, updated: 1, skipped: [], failed: [] });
    wrap(<CloudPresetsDialog open onOpenChange={() => {}} canWrite canAdmin />);

    expect(await screen.findByText("My PLA")).toBeInTheDocument();
    expect(screen.getByText("New")).toBeInTheDocument();
    expect(screen.getByText("Changed in cloud")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Diff Quality" }));
    expect(await screen.findByText("layer_height")).toBeInTheDocument();
    expect(screen.getByText("0.16")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Resync Quality" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/cloud-presets/orca/sync", {
        preset_ids: ["b"],
        overwrite_local: false,
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("0 new, 1 updated");
  });

  it("imports all new and changed, honouring the overwrite toggle", async () => {
    mockCloud({ orca: { connected: true, account: null, needs_reconnect: false, pending: false } });
    postMock.mockResolvedValue({ created: 1, updated: 1, skipped: [], failed: [] });
    wrap(<CloudPresetsDialog open onOpenChange={() => {}} canWrite canAdmin />);
    fireEvent.click(await screen.findByLabelText("Overwrite local edits"));
    fireEvent.click(await screen.findByRole("button", { name: /Import new & changed \(2\)/ }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/cloud-presets/orca/sync", {
        preset_ids: undefined,
        overwrite_local: true,
      }),
    );
  });

  it("starts Orca pairing and shows the code", async () => {
    mockCloud({});
    postMock.mockResolvedValue({
      user_code: "ABCD-1234",
      verification_uri: "https://orca/verify",
      verification_uri_complete: "https://orca/verify?c=1",
      expires_in: 600,
      interval: 5,
    });
    wrap(<CloudPresetsDialog open onOpenChange={() => {}} canWrite canAdmin />);
    fireEvent.click(await screen.findByRole("button", { name: "Connect Orca Cloud" }));
    expect(await screen.findByText("ABCD-1234")).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/cloud-presets/orca/device/start");
    expect(screen.getByRole("link", { name: "Open the approval page" })).toHaveAttribute(
      "href",
      "https://orca/verify?c=1",
    );
  });

  it("points Bambu at Settings when no account is connected, and hides writes without permission", async () => {
    mockCloud({ orca: { connected: true, account: null, needs_reconnect: false, pending: false } });
    wrap(<CloudPresetsDialog open onOpenChange={() => {}} canWrite={false} canAdmin={false} />);
    await screen.findByText("My PLA");
    expect(screen.queryByRole("button", { name: /Import new/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disconnect" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Bambu Cloud" }));
    expect(await screen.findByText(/Connect your Bambu Lab account under Settings/)).toBeInTheDocument();
  });
});
