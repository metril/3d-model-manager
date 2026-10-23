import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import type { HaLink, HaSettings } from "@/api/types";
import { HomeAssistantCard } from "@/components/settings/HomeAssistantCard";

const { getMock, postMock, putMock, patchMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  patchMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock, put: putMock, patch: patchMock, delete: deleteMock },
  };
});

// Radix's Select never reaches an interactive open state under jsdom --
// same native-<select> workaround as MaterialsSection.test.tsx.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
    ...rest
  }: {
    value?: string;
    onValueChange: (value: string) => void;
    children?: ReactNode;
  } & Record<string, unknown>) => (
    <select value={value} onChange={(event) => onValueChange(event.target.value)} {...rest}>
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children?: ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}));

function fakeSettings(overrides: Partial<HaSettings> = {}): HaSettings {
  return {
    ha_mqtt_url: "mqtt://broker:1883",
    ha_mqtt_user: "ha",
    has_mqtt_password: true,
    ha_discovery_prefix: "homeassistant",
    ha_url: "http://homeassistant.local:8123",
    has_token: true,
    ha_actor_user_id: null,
    ha_actor_username: null,
    actor_ok: true,
    actor_error: null,
    pull_status: { ok: true },
    bridge_status: { connected: true, error: null, at: 1700000000 },
    ...overrides,
  };
}

function mockGet(settings: HaSettings, links: HaLink[] = []) {
  getMock.mockImplementation((path: string) => {
    if (path === "/settings/home-assistant") return Promise.resolve(settings);
    if (path === "/settings/home-assistant/links") return Promise.resolve(links);
    if (path === "/settings/home-assistant/actors")
      return Promise.resolve([
        { id: 1, username: "admin" },
        { id: 2, username: "ha-bot" },
      ]);
    if (path === "/printers") return Promise.resolve([{ id: 1, name: "Bambu A" }]);
    return Promise.resolve({});
  });
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <HomeAssistantCard />
    </QueryClientProvider>,
  );
}

describe("HomeAssistantCard", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    putMock.mockReset();
    patchMock.mockReset();
    deleteMock.mockReset();
  });

  it("renders secrets as 'set' and never prefills the password/token inputs", async () => {
    mockGet(fakeSettings());
    renderCard();

    expect((await screen.findAllByText("(set)")).length).toBe(2);
    expect(screen.getByLabelText(/^Password/)).toHaveValue("");
    expect(screen.getByLabelText(/^Token/)).toHaveValue("");
  });

  it("calls the MQTT test endpoint and shows the result", async () => {
    mockGet(fakeSettings());
    postMock.mockImplementation((path: string) => {
      if (path === "/settings/home-assistant/test-mqtt") return Promise.resolve({ ok: true, detail: "connected" });
      return Promise.resolve({});
    });

    renderCard();
    await screen.findByLabelText("Broker URL");
    fireEvent.click(screen.getAllByRole("button", { name: "Test connection" })[0]);

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/settings/home-assistant/test-mqtt", expect.anything()));
    expect(await screen.findByText("connected")).toBeInTheDocument();
  });

  it("calls the REST test endpoint and shows the result", async () => {
    mockGet(fakeSettings());
    postMock.mockImplementation((path: string) => {
      if (path === "/settings/home-assistant/test-rest") return Promise.resolve({ ok: false, detail: "token rejected" });
      return Promise.resolve({});
    });

    renderCard();
    await screen.findByLabelText("Home Assistant URL");
    fireEvent.click(screen.getAllByRole("button", { name: "Test connection" })[1]);

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/settings/home-assistant/test-rest", {}));
    expect(await screen.findByText("token rejected")).toBeInTheDocument();
  });

  it("saves the chosen service account and clears it on None", async () => {
    mockGet(fakeSettings());
    putMock.mockResolvedValue(fakeSettings({ ha_actor_user_id: 2, ha_actor_username: "ha-bot" }));
    renderCard();

    const select = await screen.findByLabelText("HA acts as");
    await screen.findByRole("option", { name: "ha-bot" });
    fireEvent.change(select, { target: { value: "2" } });
    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/settings/home-assistant", { ha_actor_user_id: 2 }));

    fireEvent.change(select, { target: { value: "none" } });
    await waitFor(() => expect(putMock).toHaveBeenCalledWith("/settings/home-assistant", { ha_actor_user_id: null }));
  });

  it("shows the actor warning from the settings fields, with no bridge status", async () => {
    mockGet(fakeSettings({ bridge_status: null, actor_ok: false, actor_error: "no HA service account" }));
    renderCard();

    expect(await screen.findByText("no HA service account")).toBeInTheDocument();
  });

  it("shows the lacks-control warning", async () => {
    mockGet(
      fakeSettings({
        ha_actor_user_id: 2,
        actor_ok: false,
        actor_error: "HA service account lacks printer control",
      }),
    );
    renderCard();

    expect(await screen.findByText("HA service account lacks printer control")).toBeInTheDocument();
  });

  it("shows no actor warning when actor_ok is true", async () => {
    mockGet(fakeSettings({ ha_actor_user_id: 1, ha_actor_username: "admin" }));
    renderCard();

    await screen.findByLabelText("HA acts as");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the unreachable warning when pull_status.ok is false", async () => {
    mockGet(fakeSettings({ pull_status: { ok: false } }));
    renderCard();

    expect(await screen.findByText("Home Assistant unreachable, queue holds are ignored")).toBeInTheDocument();
  });

  it("does not show the unreachable warning when pull_status.ok is true", async () => {
    mockGet(fakeSettings({ pull_status: { ok: true } }));
    renderCard();

    await screen.findByLabelText("Home Assistant URL");
    expect(screen.queryByText("Home Assistant unreachable, queue holds are ignored")).not.toBeInTheDocument();
  });

  it("shows pull_status.errors (not entity_errors)", async () => {
    mockGet(fakeSettings({ pull_status: { ok: true, errors: ["binary_sensor.missing"] } }));
    renderCard();

    expect(await screen.findByText("binary_sensor.missing")).toBeInTheDocument();
  });

  it("reads bridge_status.connected for the MQTT bridge dot/label", async () => {
    mockGet(fakeSettings({ bridge_status: { connected: false, error: "disconnected", at: 1 } }));
    renderCard();

    expect(await screen.findByLabelText("Bridge offline")).toBeInTheDocument();
  });

  it("shows the bridge as unknown when bridge_status is null", async () => {
    mockGet(fakeSettings({ bridge_status: null }));
    renderCard();

    expect(await screen.findByLabelText("Bridge unknown")).toBeInTheDocument();
  });

  it("posts the default eq/on condition when adding a door link", async () => {
    mockGet(fakeSettings());
    postMock.mockImplementation((path: string) => {
      if (path === "/settings/home-assistant/links") return Promise.resolve({ id: 9 });
      return Promise.resolve({});
    });

    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Add link" }));

    fireEvent.change(await screen.findByLabelText("Entity ID"), {
      target: { value: "binary_sensor.front_door" },
    });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "door" } });
    fireEvent.click(screen.getByRole("button", { name: "Add link" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/settings/home-assistant/links", {
        printer_id: null,
        entity_id: "binary_sensor.front_door",
        role: "door",
        alert_condition: { op: "eq", value: "on" },
        hold_queue: false,
        pause_print: false,
      }),
    );
  });

  it("blocks saving an energy_kwh link with no printer selected", async () => {
    mockGet(fakeSettings());
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: "Add link" }));
    fireEvent.change(await screen.findByLabelText("Entity ID"), { target: { value: "sensor.printer_energy" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "energy_kwh" } });
    fireEvent.click(screen.getByRole("button", { name: "Add link" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("requires a specific printer");
    expect(postMock).not.toHaveBeenCalledWith("/settings/home-assistant/links", expect.anything());
  });
});
