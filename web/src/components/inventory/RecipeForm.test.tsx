import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PrintRecipe, SlicerProfile } from "@/api/types";
import { RecipeForm } from "@/components/inventory/RecipeForm";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock } };
});

vi.mock("@/components/ui/checkbox", () => ({
  Checkbox: ({
    checked,
    onCheckedChange,
    ...rest
  }: { checked?: boolean; onCheckedChange: (checked: boolean) => void } & Record<string, unknown>) => (
    <input type="checkbox" checked={checked} onChange={(e) => onCheckedChange(e.target.checked)} {...rest} />
  ),
}));

vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
    ...rest
  }: { value?: string; onValueChange: (value: string) => void; children?: ReactNode } & Record<string, unknown>) => (
    <select value={value} onChange={(e) => onValueChange(e.target.value)} {...rest}>
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

function profile(overrides: Partial<SlicerProfile>): SlicerProfile {
  return {
    id: 1,
    name: "x",
    kind: "process",
    printer_model: "A1 mini",
    nozzle_mm: 0.4,
    source: "import",
    printer_model_key: "a1mini",
    compat: [{ model_key: "a1mini", nozzle_mm: 0.4 }],
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    data: {},
    ...overrides,
  };
}

const PROFILES: SlicerProfile[] = [
  profile({ id: 1, kind: "printer", name: "A1 mini 0.4 printer", data: { curr_bed_type: ["Textured PEI Plate"] } }),
  profile({ id: 2, name: "Mini draft" }),
  profile({ id: 3, name: "P1S draft", printer_model: "P1S", printer_model_key: "p1s", compat: [{ model_key: "p1s", nozzle_mm: 0.4 }] }),
  profile({ id: 4, name: "Mini 0.6 strong", compat: [{ model_key: "a1mini", nozzle_mm: 0.6 }] }),
  profile({ id: 5, name: "Universal fast", printer_model: null, printer_model_key: null, compat: null }),
];

function recipe(overrides: Partial<PrintRecipe> = {}): PrintRecipe {
  return {
    id: 9,
    name: "R",
    printer_profile_id: 1,
    process_profile_id: 2,
    filament_profile_ids: [null, null, null, null],
    bed_type: "Textured PEI Plate",
    options: {},
    target_printer_id: null,
    target_printer_model: null,
    fanout: "single",
    n: null,
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderForm(r: PrintRecipe) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RecipeForm open onOpenChange={() => {}} recipe={r} />
    </QueryClientProvider>,
  );
}

function processOptions(): string[] {
  const select = screen.getByLabelText("Process profile") as HTMLSelectElement;
  return Array.from(select.options).map((o) => o.text);
}

beforeEach(() => {
  getMock.mockReset();
  getMock.mockImplementation((path: string) => {
    if (path.startsWith("/profiles?")) {
      const kind = new URL(path, "http://x").searchParams.get("kind");
      const items = PROFILES.filter((p) => p.kind === kind);
      return Promise.resolve({ items, total: items.length, nozzle_source: null });
    }
    return Promise.resolve([]);
  });
});

describe("RecipeForm scope filter", () => {
  it("lists only profiles matching the printer profile's model and nozzle, plus universal", async () => {
    renderForm(recipe());
    await screen.findByRole("option", { name: "Mini draft" });
    expect(processOptions()).toEqual(["Mini draft", "Universal fast"]);
  });

  it("'Show all profiles' reveals the rest, labelled with the reason", async () => {
    renderForm(recipe());
    await screen.findByRole("option", { name: "Mini draft" });
    fireEvent.click(screen.getByLabelText("Show all profiles"));
    expect(processOptions()).toEqual([
      "Mini draft",
      "Universal fast",
      "P1S draft (for P1S)",
      "Mini 0.6 strong (for 0.6 mm nozzle)",
    ]);
  });

  it("keeps an already-selected incompatible profile visible with a warning", async () => {
    renderForm(recipe({ process_profile_id: 3 }));
    await screen.findByRole("option", { name: "P1S draft (for P1S)" });
    expect(processOptions()).toEqual(["Mini draft", "Universal fast", "P1S draft (for P1S)"]);
    expect(screen.getByRole("status")).toHaveTextContent(/doesn't match/);
  });
});
