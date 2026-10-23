import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FailureDetectionCard } from "@/components/settings/FailureDetectionCard";

const { getMock, postMock, putMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock } };
});

const SETTINGS = {
  url: "",
  enabled: false,
  interval_s: 10,
  warn: 0.38,
  pause: 0.78,
  action: "notify",
  callback_url: "http://api:8000",
};

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <FailureDetectionCard />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  [getMock, postMock, putMock].forEach((m) => m.mockReset());
  getMock.mockResolvedValue(SETTINGS);
  putMock.mockImplementation((_url: string, body: unknown) => Promise.resolve(body));
});

describe("FailureDetectionCard", () => {
  it("saves edited settings", async () => {
    renderCard();
    fireEvent.change(await screen.findByLabelText("ML API URL"), { target: { value: "http://ml:3333" } });
    fireEvent.click(screen.getByLabelText("Enable failure detection"));
    fireEvent.change(screen.getByLabelText("Pause threshold"), { target: { value: "0.6" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith(
        "/settings/obico",
        expect.objectContaining({ url: "http://ml:3333", enabled: true, pause: 0.6 }),
      ),
    );
  });

  it("blocks save when pause < warn", async () => {
    renderCard();
    fireEvent.change(await screen.findByLabelText("Pause threshold"), { target: { value: "0.1" } });
    expect(screen.getByRole("alert")).toHaveTextContent("at least the warn threshold");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("tests the connection with the draft URL", async () => {
    postMock.mockResolvedValue({ ok: true, detail: "HTTP 200" });
    renderCard();
    fireEvent.change(await screen.findByLabelText("ML API URL"), { target: { value: "http://ml:3333" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/settings/obico/test", { url: "http://ml:3333" });
  });
});
