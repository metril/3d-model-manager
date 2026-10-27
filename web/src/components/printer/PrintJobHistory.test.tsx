import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import type { ArchiveOut } from "@/api/types";
import { PrintJobHistory } from "@/components/printer/PrintJobHistory";

const { useArchivesMock } = vi.hoisted(() => ({ useArchivesMock: vi.fn() }));
vi.mock("@/components/tags/EntityTags", () => ({ EntityTags: () => null }));
vi.mock("@/components/tags/TagFilter", async (orig) => ({
  ...(await orig<typeof import("@/components/tags/TagFilter")>()),
  TagFilter: () => null,
}));
vi.mock("@/components/print/SourceFiles", () => ({ SourceFiles: () => null }));
vi.mock("@/api/archives", () => ({
  useArchives: useArchivesMock,
  archiveThumbnailUrl: (id: number) => `/t/${id}`,
}));
vi.mock("@/api/printers", () => ({
  usePrinters: () => ({ data: [{ id: 1, name: "A1" }] }),
}));
vi.mock("@/components/print/RunDetailSheet", () => ({
  RunDetailSheet: ({ archiveId }: { archiveId: number | null }) =>
    archiveId != null ? <div role="dialog">Run {archiveId}</div> : null,
}));
vi.mock("@/components/ui/select", () => ({
  Select: ({ value, onValueChange, children, ...rest }: { value?: string; onValueChange: (v: string) => void; children?: ReactNode } & Record<string, unknown>) => (
    <select value={value} onChange={(e) => onValueChange(e.target.value)} {...rest}>{children}</select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children?: ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children?: ReactNode }) => <option value={value}>{children}</option>,
}));

const rows = [
  {
    id: 3, model_name: "Benchy", subtask_name: "x.3mf", printer_id: 1, printer_name: "A1",
    result: "completed", duration_s: 3600, has_thumbnail: true, finished_at: null, created_at: "2026-01-01T00:00:00Z",
    run: { quality: "good", filament_g: 12.5, total_cost: 8 },
  },
  {
    id: 4, model_name: null, subtask_name: "ext.3mf", printer_id: 1, printer_name: "A1",
    result: "failed", duration_s: null, has_thumbnail: false, finished_at: null, created_at: "2026-01-01T00:00:00Z",
    run: null,
  },
] as unknown as ArchiveOut[];

beforeEach(() => {
  useArchivesMock.mockReset().mockReturnValue({ data: rows, isLoading: false, isError: false });
});

describe("PrintJobHistory", () => {
  it("renders a row per archive", () => {
    render(<PrintJobHistory />);
    expect(screen.getByText("Benchy")).toBeInTheDocument();
    expect(screen.getByText("ext.3mf")).toBeInTheDocument();
    expect(screen.getAllByText("completed").length).toBeGreaterThan(0);
    expect(screen.getByText("8.00")).toBeInTheDocument();
    expect(screen.getByText("12.5 g")).toBeInTheDocument();
  });
  it("opens the sheet on row click", () => {
    render(<PrintJobHistory />);
    fireEvent.click(screen.getByText("Benchy"));
    expect(screen.getByRole("dialog")).toHaveTextContent("Run 3");
  });
  it("result filter narrows the query", () => {
    render(<PrintJobHistory />);
    fireEvent.change(screen.getByLabelText("Result filter"), { target: { value: "failed" } });
    expect(useArchivesMock).toHaveBeenLastCalledWith(expect.objectContaining({ result: "failed" }));
  });

  it("forwards a printerId prop to useArchives", () => {
    render(<PrintJobHistory printerId={2} />);
    expect(useArchivesMock).toHaveBeenLastCalledWith(expect.objectContaining({ printerId: 2 }));
  });

  it("passes the calendar day as from/to", () => {
    render(<PrintJobHistory date="2026-01-01" />);
    expect(useArchivesMock).toHaveBeenLastCalledWith(expect.objectContaining({ from: "2026-01-01", to: "2026-01-01" }));
  });
});
