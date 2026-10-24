import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { CsvImportDialog } from "@/components/inventory/CsvImportDialog";

const { postMock, formMock } = vi.hoisted(() => ({ postMock: vi.fn(), formMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, postForm: formMock } };
});

const preview = {
  token: "tok1",
  rows: [
    { line: 2, action: "create", errors: [], warnings: ["unknown brand"] },
    { line: 3, action: "update", spool_id: 7, errors: [], warnings: [] },
    { line: 4, action: "error", errors: ["bad weight"], warnings: [] },
  ],
  summary: { create: 1, update: 1, error: 1 },
};

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <CsvImportDialog open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  );
  return onOpenChange;
}
const pick = () =>
  fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [new File(["a,b"], "s.csv", { type: "text/csv" })] } });

describe("CsvImportDialog", () => {
  beforeEach(() => {
    postMock.mockReset();
    formMock.mockReset();
  });

  it("dry-runs the file, shows the diff, then commits the token", async () => {
    formMock.mockResolvedValue(preview);
    postMock.mockResolvedValue({ created: 1, updated: 1, errors: 1 });
    const onOpenChange = setup();
    expect(screen.getByRole("button", { name: "Commit import" })).toBeDisabled();

    pick();
    expect(await screen.findByText("bad weight")).toBeInTheDocument();
    expect(screen.getByText("unknown brand")).toBeInTheDocument();
    expect(screen.getByTestId("csv-summary")).toHaveTextContent("1 to create, 1 to update, 1 with errors");
    const [path, form] = formMock.mock.calls[0] as [string, FormData];
    expect(path).toBe("/spools/import?dry_run=true");
    expect((form.get("file") as File).name).toBe("s.csv");

    fireEvent.click(screen.getByRole("button", { name: "Commit import" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/spools/import/commit", { token: "tok1" }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("offers a re-check when the commit token has expired (410)", async () => {
    formMock.mockResolvedValue(preview);
    postMock.mockRejectedValue(new ApiError(410, "expired"));
    setup();
    pick();
    await screen.findByText("bad weight");
    fireEvent.click(screen.getByRole("button", { name: "Commit import" }));
    expect(await screen.findByText(/preview expired/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Re-check" }));
    await waitFor(() => expect(formMock).toHaveBeenCalledTimes(2));
  });
});
