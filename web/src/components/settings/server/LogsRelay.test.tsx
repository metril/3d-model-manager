import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MqttDebugModal } from "@/components/printer/MqttDebugModal";
import { matchesFilter } from "@/lib/mqttFilter";
import { LogsCard } from "@/components/settings/server/LogsCard";
import { MqttRelayCard } from "@/components/settings/server/MqttRelayCard";

const { getMock, postMock, putMock, toastMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock } };
});
vi.mock("sonner", () => ({ toast: toastMock }));

const RELAY = {
  enabled: true, host: "broker.local", port: 1883, tls: false, tls_verify: true, username: "u", has_password: true,
  topic_prefix: "tdmm", commands_enabled: false, actor_user_id: null, actor_username: null, actor_ok: true, actor_error: null,
  status: { enabled: true, connected: true, error: null, actor_ok: true, actor_error: null, published: 12, at: 1 },
};

function wrap(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

class FakeES {
  static last: FakeES | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  closed = false;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeES.last = this;
  }
  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  putMock.mockReset();
  FakeES.last = null;
  vi.stubGlobal("EventSource", FakeES);
});

describe("MqttRelayCard", () => {
  it("shows status and saves with the masked password kept", async () => {
    getMock.mockImplementation((p: string) =>
      Promise.resolve(p === "/settings/mqtt-relay/actors" ? [] : RELAY),
    );
    putMock.mockResolvedValue(RELAY);
    wrap(<MqttRelayCard />);
    expect(await screen.findByTestId("relay-status")).toHaveTextContent("Connected. 12 messages published.");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const body = putMock.mock.calls[0][1];
    expect(body).toMatchObject({ host: "broker.local", password: "***", topic_prefix: "tdmm", commands_enabled: false });
  });

  it("requires a service user when commands are on", async () => {
    getMock.mockImplementation((p: string) =>
      Promise.resolve(p === "/settings/mqtt-relay/actors" ? [{ id: 3, username: "svc" }] : RELAY),
    );
    wrap(<MqttRelayCard />);
    await screen.findByTestId("relay-status");
    fireEvent.click(screen.getByLabelText("Accept commands"));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(await screen.findByLabelText("Service user"), { target: { value: "3" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });
});

describe("LogsCard", () => {
  it("renders lines and filters by level", async () => {
    getMock.mockResolvedValue({
      entries: [{ id: 1, ts: 1700000000, level: "ERROR", logger: "app.x", service: "api", msg: "disk full" }],
      last_id: 1,
      services: ["api"],
      capacity: 5000,
    });
    wrap(<LogsCard />);
    expect(await screen.findByText(/disk full/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Minimum level"), { target: { value: "ERROR" } });
    await waitFor(() => expect(getMock.mock.calls.some((c) => String(c[0]).includes("level=ERROR"))).toBe(true));
  });
});

describe("MqttDebugModal", () => {
  const entry = (payload: unknown, dir = "in", topic = "device/S/report") => ({ ts: 1, dir, topic, payload });

  it("matchesFilter checks keys, values and topic", () => {
    const e = entry({ print: { gcode_state: "RUNNING" } });
    expect(matchesFilter(e, "gcode_state")).toBe(true);
    expect(matchesFilter(e, "running")).toBe(true);
    expect(matchesFilter(e, "report")).toBe(true);
    expect(matchesFilter(e, "nozzle")).toBe(false);
  });

  it("loads history, streams, pauses and filters", async () => {
    getMock.mockResolvedValue([entry({ old: 1 })]);
    wrap(<MqttDebugModal printerId={7} printerName="A1" open onOpenChange={() => undefined} />);
    expect(await screen.findByText(/device\/S\/report/)).toBeInTheDocument();
    expect(FakeES.last?.url).toBe("/api/admin/printers/7/mqtt/stream");
    FakeES.last!.onmessage!({ data: JSON.stringify(entry({ fresh: 2 }, "cmd", "pause")) });
    expect(await screen.findByText("pause")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    FakeES.last!.onmessage!({ data: JSON.stringify(entry({ x: 1 }, "in", "while-paused")) });
    expect(screen.queryByText("while-paused")).toBeNull();
    fireEvent.change(screen.getByLabelText("Filter by key or value"), { target: { value: "fresh" } });
    expect(screen.queryByText("device/S/report")).toBeNull();
    expect(screen.getByText("pause")).toBeInTheDocument();
  });
});
