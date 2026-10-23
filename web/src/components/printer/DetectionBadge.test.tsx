import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DetectionBadge, DetectionBadgeView } from "@/components/printer/DetectionBadge";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

const status = (o: object) => ({
  active: true,
  state: "safe" as const,
  score: 0.1,
  smoothed: 0.12,
  detection_paused: false,
  ...o,
});

describe("DetectionBadgeView", () => {
  it("shows the smoothed score", () => {
    render(<DetectionBadgeView status={status({})} />);
    expect(screen.getByText("AI 0.12")).toBeInTheDocument();
  });
  it("shows detection paused", () => {
    render(<DetectionBadgeView status={status({ active: false, detection_paused: true })} />);
    expect(screen.getByText("detection paused")).toBeInTheDocument();
  });
  it("renders nothing when idle", () => {
    const { container } = render(<DetectionBadgeView status={status({ active: false, smoothed: null })} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("DetectionBadge", () => {
  beforeEach(() => getMock.mockReset());
  it("reads the printer's status", async () => {
    getMock.mockResolvedValue({ status: status({ smoothed: 0.5, state: "warn" }) });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <DetectionBadge printerId={2} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("AI 0.50")).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith("/printers/2/detection");
  });
});
