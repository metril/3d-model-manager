import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LabelsDialog } from "@/components/inventory/LabelsDialog";

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }));
vi.mock("@/components/ui/select", () => import("@/test/selectMock"));

function lastCall(url: string): unknown {
  const calls = fetchMock.mock.calls.filter((c) => c[0] === url);
  const last = calls.at(-1);
  return last ? JSON.parse((last[1] as { body: string }).body) : undefined;
}

describe("LabelsDialog", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["x"], { type: "image/png" })) });
    vi.stubGlobal("fetch", fetchMock);
    URL.createObjectURL = vi.fn(() => "blob:preview");
    URL.revokeObjectURL = vi.fn();
  });

  function setup() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <LabelsDialog ids={[1, 2]} open onOpenChange={vi.fn()} />
      </QueryClientProvider>,
    );
  }

  it("requests a preview with the chosen template/page and re-requests on change", async () => {
    setup();
    await waitFor(() => expect(lastCall("/api/spools/labels/preview")).toEqual({ ids: [1, 2], template: "tight", page: "a4" }));
    expect(await screen.findByAltText("Label preview")).toHaveAttribute("src", "blob:preview");
    fireEvent.change(screen.getByLabelText("Template"), { target: { value: "roomy" } });
    await waitFor(() => expect(lastCall("/api/spools/labels/preview")).toEqual({ ids: [1, 2], template: "roomy", page: "a4" }));
  });

  it("downloads in the selected format", async () => {
    setup();
    await screen.findByAltText("Label preview");
    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "png" } });
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() =>
      expect(lastCall("/api/spools/labels")).toEqual({ ids: [1, 2], template: "tight", page: "a4", format: "png" }),
    );
  });
});
