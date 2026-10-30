import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QueueBehaviourCard } from "@/components/settings/QueueBehaviourCard";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, put: putMock } };
});

beforeEach(() => {
  getMock.mockReset().mockResolvedValue({ require_plate_clear: false, pause_heads_up_min: 5 });
  putMock.mockReset().mockImplementation((_p: string, body: unknown) => Promise.resolve(body));
});

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <QueueBehaviourCard />
    </QueryClientProvider>,
  );
}

describe("QueueBehaviourCard", () => {
  it("saves the heads-up minutes", async () => {
    renderCard();
    fireEvent.change(await screen.findByLabelText("Pause heads-up (min)"), { target: { value: "15" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings/queue", { require_plate_clear: false, pause_heads_up_min: 15 }),
    );
  });

  it("rejects out-of-range minutes", async () => {
    renderCard();
    fireEvent.change(await screen.findByLabelText("Pause heads-up (min)"), { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("1-120");
    expect(putMock).not.toHaveBeenCalled();
  });
});
