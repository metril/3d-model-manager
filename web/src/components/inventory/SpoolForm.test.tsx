import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpoolForm } from "@/components/inventory/SpoolForm";

const { getMock, postMock, putMock, patchMock, authMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  patchMock: vi.fn(),
  authMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock, patch: patchMock } };
});

vi.mock("@/components/inventory/SupplierListEditor", () => ({
  SupplierListEditor: ({ onChange }: { onChange: (v: unknown[]) => void }) => (
    <button
      type="button"
      onClick={() =>
        onChange([{ supplier_id: 1, article_number: null, quoted_price_per_kg: null, is_purchase_source: false }])
      }
    >
      add-supplier
    </button>
  ),
}));

vi.mock("@/api/auth", () => ({ useAuth: () => authMock() }));

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

function renderForm(props: Partial<React.ComponentProps<typeof SpoolForm>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <SpoolForm open onOpenChange={onOpenChange} {...props} />
    </QueryClientProvider>,
  );
  return onOpenChange;
}

describe("SpoolForm", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    putMock.mockReset();
    patchMock.mockReset();
    authMock.mockReset();
    getMock.mockResolvedValue([]);
    authMock.mockReturnValue({ can: () => true, canPrinter: () => true });
  });

  it("computes used grams from a gross-weight reading via Weigh spool", async () => {
    renderForm();

    fireEvent.change(screen.getByLabelText("Empty spool weight (g)"), { target: { value: "250" } });
    fireEvent.change(screen.getByLabelText("Weigh spool (gross g, on a scale)"), { target: { value: "650" } });

    // label 1000, empty 250, gross 650 -> used = 1000 - (650 - 250) = 600
    expect(await screen.findByText("= 600.0 g used")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.getByLabelText("Used (g)")).toHaveValue(600);
  });

  it("hides the Add/Save button for a viewer without inventory:write", () => {
    authMock.mockReturnValue({ can: () => false, canPrinter: () => true });
    renderForm();

    expect(screen.queryByRole("button", { name: "Add spool" })).toBeNull();
  });

  it("shows the Add spool button for a writer", () => {
    renderForm();
    expect(screen.getByRole("button", { name: "Add spool" })).toBeInTheDocument();
  });

  it("retries without a second create (PATCHing edits) after a created spool's supplier save fails", async () => {
    postMock.mockResolvedValue({ id: 7, suppliers: [] });
    putMock.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce([]);
    patchMock.mockResolvedValue({ id: 7, suppliers: [] });
    const onOpenChange = renderForm();

    fireEvent.click(screen.getByRole("button", { name: "add-supplier" }));
    fireEvent.click(screen.getByRole("button", { name: "Add spool" }));
    await waitFor(() => expect(putMock).toHaveBeenCalledTimes(1));
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    fireEvent.click(screen.getByRole("button", { name: "Add spool" }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(putMock).toHaveBeenCalledTimes(2);
    expect(postMock).toHaveBeenCalledTimes(1);
    expect(patchMock).toHaveBeenCalledWith(expect.stringContaining("/spools/7"), expect.anything());
  });
});
