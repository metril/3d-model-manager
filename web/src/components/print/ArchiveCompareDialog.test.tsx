import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ArchiveCompareDialog, CompareBar } from "@/components/print/ArchiveCompareDialog";
import { arch, renderQ } from "@/components/print/archiveTestUtils";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock("@/api/client", async (orig) => ({
  ...(await orig<typeof import("@/api/client")>()),
  api: { get: getMock },
}));

describe("ArchiveCompareDialog", () => {
  it("shows differing settings and correlation", async () => {
    getMock.mockResolvedValue({
      archives: [arch({ id: 1, model_name: "A" }), arch({ id: 2, model_name: "B", result: "failed" })],
      settings_diff: [{ key: "layer_height", values: { "1": 0.2, "2": 0.1 } }],
      filament: { "1": [{ grams: 10 }], "2": [{ grams: 12 }] },
      time: { "1": { estimated_s: 3600, actual_s: 3700 }, "2": { estimated_s: null, actual_s: null } },
      correlation: [{ key: "layer_height", value: 0.2, success_rate: 0.75, n: 8 }],
    });
    renderQ(<ArchiveCompareDialog ids={[1, 2]} open onOpenChange={() => {}} />);
    expect(await screen.findByText("layer_height", { selector: "th" })).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith("/archives/compare?ids=1,2");
    expect(screen.getAllByTestId("diff-row")).toHaveLength(1);
    expect(screen.getByTestId("corr-row")).toHaveTextContent("75%");
    expect(screen.getByTestId("corr-row")).toHaveTextContent("8");
    expect(screen.getByText("12 g")).toBeInTheDocument();
  });

  it("CompareBar needs 2 selections", () => {
    const sel = { ids: [1], toggle: vi.fn(), clear: vi.fn() };
    const { unmount } = renderQ(<CompareBar selection={sel} />);
    expect(screen.getByRole("button", { name: "Compare (1)" })).toBeDisabled();
    unmount();
    renderQ(<CompareBar selection={{ ...sel, ids: [1, 2] }} />);
    expect(screen.getByRole("button", { name: "Compare (2)" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(sel.clear).toHaveBeenCalled();
  });
});
