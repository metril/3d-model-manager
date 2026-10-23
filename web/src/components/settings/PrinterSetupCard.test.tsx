import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Features, PrinterOut } from "@/api/types";
import { PrinterSetupCard } from "@/components/settings/PrinterSetupCard";

// `vi.mock` factories are hoisted above the module's own top-level bindings
// (same pattern as SettingsPage.test.tsx/ScanReport.test.tsx), so the fakes
// have to be created through `vi.hoisted`.
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

const PRINTER: PrinterOut = {
  id: 1,
  name: "Bambu A1",
  kind: "bambu_lan",
  host: "192.168.1.50",
  serial: "AC12345",
  model: "A1 mini",
  enabled: true,
  options: {},
  access_code_set: true,
  build_volume_mm: { x: 256, y: 256, z: 256 },
};

// Only `printer_enabled` matters to this card -- `Pick` keeps every call
// site above from having to also stub the Round 8 T6 slicer-watch fields.
function mockApi(features: Pick<Features, "printer_enabled">, printers: PrinterOut[] = []) {
  getMock.mockImplementation((path: string) => {
    if (path === "/features") return Promise.resolve(features);
    if (path === "/printers") return Promise.resolve(printers);
    return Promise.resolve([]);
  });
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PrinterSetupCard />
    </QueryClientProvider>,
  );
}

describe("PrinterSetupCard", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    patchMock.mockReset();
    deleteMock.mockReset();
  });

  it("keeps an existing free-text model selectable alongside the grouped options", async () => {
    mockApi({ printer_enabled: true }, [{ ...PRINTER, model: "Custom Foo" }]);
    renderCard();
    const select = (await screen.findByDisplayValue("Custom Foo")) as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    expect(select.querySelectorAll("optgroup").length).toBe(7);
  });

  it("shows the disabled note when the printer feature flag is off", async () => {
    mockApi({ printer_enabled: false });

    renderCard();

    expect(await screen.findByText(/Printer integration is off/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Name/)).not.toBeInTheDocument();
  });

  it("blocks Add printer and shows inline errors when required fields are blank", async () => {
    mockApi({ printer_enabled: true }, []);

    renderCard();

    await screen.findByText("Add a printer");
    fireEvent.click(screen.getByRole("button", { name: "Add printer" }));

    expect(await screen.findByText("Name is required.")).toBeInTheDocument();
    expect(screen.getByText("Host is required.")).toBeInTheDocument();
    expect(screen.getByText("Serial is required.")).toBeInTheDocument();
    expect(screen.getByText("Access code is required.")).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
  });

  it("submits all fields once the required create fields are filled", async () => {
    mockApi({ printer_enabled: true }, []);
    postMock.mockImplementation((path: string) => {
      if (path === "/printers") return Promise.resolve({ ...PRINTER, id: 2 });
      return Promise.resolve({});
    });

    renderCard();

    await screen.findByText("Add a printer");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New Printer" } });
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "192.168.1.99" } });
    fireEvent.change(screen.getByLabelText("Serial"), { target: { value: "SN999999" } });
    fireEvent.change(screen.getByLabelText("Access code"), { target: { value: "topsecret" } });

    fireEvent.click(screen.getByRole("button", { name: "Add printer" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers", expect.any(Object)));
    const call = postMock.mock.calls.find(([path]) => path === "/printers") as [string, Record<string, unknown>];
    expect(call[1]).toMatchObject({
      name: "New Printer",
      host: "192.168.1.99",
      serial: "SN999999",
      access_code: "topsecret",
    });
  });

  it("blocks Save changes on an existing printer when serial is cleared", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);

    renderCard();

    await screen.findByDisplayValue("Bambu A1");
    fireEvent.change(screen.getByDisplayValue("AC12345"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Serial is required.")).toBeInTheDocument();
    expect(patchMock).not.toHaveBeenCalled();
  });

  it("omits access_code from the PATCH body when the field is left blank", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);
    patchMock.mockResolvedValue({ ...PRINTER, name: "Renamed" });

    renderCard();

    await screen.findByDisplayValue("Bambu A1");
    fireEvent.change(screen.getByDisplayValue("Bambu A1"), { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    const [, body] = patchMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).not.toHaveProperty("access_code");
    expect(body.name).toBe("Renamed");
  });

  it("sends access_code in the PATCH body once the user types a new value", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);
    patchMock.mockResolvedValue(PRINTER);

    renderCard();

    await screen.findByDisplayValue("Bambu A1");
    // Both the existing printer's editor and the "Add a printer" form below
    // it have an "Access code" field -- the existing printer's is first.
    fireEvent.change(screen.getAllByLabelText("Access code")[0], { target: { value: "new-code" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    const [, body] = patchMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(body.access_code).toBe("new-code");
  });

  it("Test connection surfaces ProbeOut.detail", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);
    postMock.mockResolvedValue({ ok: true, detail: "connected via LAN", gcode_state: "IDLE" });

    renderCard();

    await screen.findByDisplayValue("Bambu A1");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByText(/connected via LAN/)).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/printers/1/test");
  });

  it("Detect fills the serial field on success", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);
    postMock.mockImplementation((path: string) => {
      if (path === "/printers/detect-serial") {
        return Promise.resolve({
          serial: "0309CA410600958",
          detail: "Detected serial from the printer's certificate.",
        });
      }
      return Promise.resolve({});
    });

    renderCard();

    await screen.findByDisplayValue("Bambu A1");
    // The existing printer's editor and the "Add a printer" form below it
    // both have a Detect button -- the existing printer's is first.
    fireEvent.click(screen.getAllByRole("button", { name: "Detect" })[0]);

    expect(await screen.findByDisplayValue("0309CA410600958")).toBeInTheDocument();
    expect(await screen.findByText(/Detected serial from the printer's certificate/)).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/printers/detect-serial", { host: "192.168.1.50" });
  });

  it("Detect shows the detail as an error note when no serial is found", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);
    postMock.mockImplementation((path: string) => {
      if (path === "/printers/detect-serial") {
        return Promise.resolve({
          serial: null,
          detail: "Reached the printer but its certificate had no serial.",
        });
      }
      return Promise.resolve({});
    });

    renderCard();

    await screen.findByDisplayValue("Bambu A1");
    fireEvent.click(screen.getAllByRole("button", { name: "Detect" })[0]);

    const note = await screen.findByText(/Reached the printer but its certificate had no serial/);
    expect(note).toBeInTheDocument();
    expect(note).toHaveAttribute("role", "alert");
    // The serial field must NOT have been touched.
    expect(screen.getByDisplayValue("AC12345")).toBeInTheDocument();
  });

  it("disables Detect while the host is blank", async () => {
    mockApi({ printer_enabled: true }, []);

    renderCard();

    await screen.findByText("Add a printer");
    expect(screen.getByRole("button", { name: "Detect" })).toBeDisabled();
  });

  it("seeds the build-volume fields from the printer's build_volume_mm", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);

    renderCard();

    await screen.findByDisplayValue("Bambu A1");
    expect(screen.getAllByDisplayValue("256")).toHaveLength(3);
  });

  it("includes build_volume_mm in the PATCH body once all three dimensions are filled", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);
    patchMock.mockResolvedValue(PRINTER);

    renderCard();

    await screen.findByDisplayValue("Bambu A1");
    fireEvent.change(screen.getAllByLabelText("Build volume X (mm)")[0], { target: { value: "300" } });
    fireEvent.change(screen.getAllByLabelText("Y (mm)")[0], { target: { value: "300" } });
    fireEvent.change(screen.getAllByLabelText("Z (mm)")[0], { target: { value: "400" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    const [, body] = patchMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(body.build_volume_mm).toEqual({ x: 300, y: 300, z: 400 });
  });

  it("omits build_volume_mm from the PATCH body when all three fields are blank", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);
    patchMock.mockResolvedValue(PRINTER);

    renderCard();

    await screen.findByDisplayValue("Bambu A1");
    fireEvent.change(screen.getAllByLabelText("Build volume X (mm)")[0], { target: { value: "" } });
    fireEvent.change(screen.getAllByLabelText("Y (mm)")[0], { target: { value: "" } });
    fireEvent.change(screen.getAllByLabelText("Z (mm)")[0], { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    const [, body] = patchMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).not.toHaveProperty("build_volume_mm");
  });

  it("shows a validation error when only some build-volume dimensions are filled", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);

    renderCard();

    await screen.findByDisplayValue("Bambu A1");
    fireEvent.change(screen.getAllByLabelText("Y (mm)")[0], { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Enter all three dimensions, or leave all blank.")).toBeInTheDocument();
    expect(patchMock).not.toHaveBeenCalled();
  });

  async function fillNew() {
    fireEvent.change(await screen.findByLabelText("Name", { selector: "#printer-new-name" }), { target: { value: "P" } });
    fireEvent.change(document.getElementById("printer-new-host")!, { target: { value: "10.0.0.9" } });
    fireEvent.change(document.getElementById("printer-new-serial")!, { target: { value: "SER9" } });
    fireEvent.change(document.getElementById("printer-new-access-code")!, { target: { value: "code" } });
  }

  it("discovers printers and click-to-fills the add form", async () => {
    mockApi({ printer_enabled: true }, [PRINTER]);
    postMock.mockResolvedValue([{ host: "192.168.1.77", serial: "DISC1", model_hint: "A1 mini", source: "ssdp" }]);
    renderCard();
    const subnet = (await screen.findByLabelText("Subnet")) as HTMLInputElement;
    await waitFor(() => expect(subnet.value).toBe("192.168.1.0/24"));
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    fireEvent.click(await screen.findByText(/192\.168\.1\.77/));
    expect(postMock).toHaveBeenCalledWith("/printers/discover", { cidr: "192.168.1.0/24" });
    await waitFor(() => expect((document.getElementById("printer-new-host") as HTMLInputElement).value).toBe("192.168.1.77"));
    expect((document.getElementById("printer-new-serial") as HTMLInputElement).value).toBe("DISC1");
  });

  it("shows preflight failures with Back / Save anyway instead of blocking", async () => {
    mockApi({ printer_enabled: true });
    postMock.mockImplementation((path: string) =>
      path === "/printers/diagnostic"
        ? Promise.resolve({ ok: false, checks: [{ id: "mqtt_auth", status: "fail", hint: "Wrong access code", detail: null }] })
        : Promise.resolve({ ...PRINTER, id: 2 }),
    );
    renderCard();
    await fillNew();
    fireEvent.click(screen.getByRole("button", { name: "Add printer" }));
    expect(await screen.findByText("Wrong access code")).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalledWith("/printers", expect.anything());
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.queryByText("Wrong access code")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add printer" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save anyway" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers", expect.objectContaining({ host: "10.0.0.9" })));
  });
});
