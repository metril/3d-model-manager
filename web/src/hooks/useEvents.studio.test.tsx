import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EventsProvider } from "@/hooks/useEvents";

const { toastMock } = vi.hoisted(() => ({ toastMock: vi.fn() }));
vi.mock("sonner", () => ({ toast: Object.assign(toastMock, { success: vi.fn(), error: vi.fn() }) }));

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  readonly url: string;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() {}
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <EventsProvider>
        <div>ready</div>
      </EventsProvider>
    </QueryClientProvider>,
  );
  const send = (e: object) => FakeEventSource.instances[0].onmessage?.({ data: JSON.stringify(e) } as MessageEvent<string>);
  return { spy, send };
}

describe("studio events", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    toastMock.mockReset();
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("toasts with a model link and invalidates the model's detail on file_returned", () => {
    const { spy, send } = setup();
    send({ type: "studio.file_returned", model_slug: "m", model_id: 1, file_id: 5, name: "a.3mf", sliced: false });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["models", "detail", "m"] });
    expect(spy).not.toHaveBeenCalledWith({ queryKey: ["models", "list"] });
    expect(toastMock).toHaveBeenCalledWith("a.3mf returned from OrcaSlicer", expect.objectContaining({ action: expect.objectContaining({ label: "View model" }) }));
  });

  it("also refreshes the library list when the returned file is sliced", () => {
    const { spy, send } = setup();
    send({ type: "studio.file_returned", model_slug: "m", model_id: 1, file_id: 5, name: "a.gcode.3mf", sliced: true });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["models", "list"] });
  });

  it("invalidates the session query on session_started", () => {
    const { spy, send } = setup();
    send({ type: "studio.session_started", token: "t", model_slug: "m", file_name: "a.3mf" });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["studio", "session"] });
    expect(toastMock).not.toHaveBeenCalled();
  });
});
