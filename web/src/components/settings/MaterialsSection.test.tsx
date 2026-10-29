import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { MaterialsSection } from "@/components/settings/MaterialsSection";
import type { MaterialOut } from "@/api/types";

// Same hoisted-mock pattern as CategoriesSection.test.tsx / PrintsTab.test.tsx.
const { getMock, postMock, patchMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  patchMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    api: { ...actual.api, get: getMock, post: postMock, patch: patchMock, delete: deleteMock },
  };
});

// Radix's Select never reaches an interactive open state under jsdom -- swap
// it for a plain native <select>, same workaround as PrintsTab.test.tsx.
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

function material(overrides: Partial<MaterialOut> = {}): MaterialOut {
  return {
    id: 1,
    name: "Galaxy Black",
    kind: "PLA",
    color: "#111111",
    vendor: "Bambu",
    notes: null,
    print_count: 2,
    ...overrides,
  };
}

function setupGet(materials: MaterialOut[] = []) {
  getMock.mockImplementation((path: string) => {
    if (path === "/materials") return Promise.resolve(materials);
    return Promise.resolve([]);
  });
}

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MaterialsSection />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockReset();
  setupGet();
  postMock.mockReset().mockResolvedValue(material());
  patchMock.mockReset().mockResolvedValue(material());
  deleteMock.mockReset().mockResolvedValue(undefined);
});

describe("MaterialsSection -- list", () => {
  it("shows a loading skeleton then the empty state when there are no materials", async () => {
    setupGet([]);
    renderSection();

    expect(await screen.findByText("No materials yet.")).toBeInTheDocument();
  });

  it("renders materials with kind, vendor, and print count", async () => {
    setupGet([material()]);
    renderSection();

    expect(await screen.findByText("Galaxy Black")).toBeInTheDocument();
    expect(screen.getByText("PLA")).toBeInTheDocument();
    expect(screen.getByText("Bambu")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});

describe("MaterialsSection -- add", () => {
  it("submits the add-material dialog with the entered fields", async () => {
    setupGet([]);
    renderSection();

    fireEvent.click(await screen.findByRole("button", { name: "Add material" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Clear PETG" } });
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "PETG" } });
    fireEvent.click(screen.getByLabelText("Color"));
    fireEvent.change(screen.getByLabelText("Hex color"), { target: { value: "#ffffff" } });
    fireEvent.keyDown(screen.getByLabelText("Hex color"), { key: "Escape" });
    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "Polymaker" } });

    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add material" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledExactlyOnceWith("/materials", {
        name: "Clear PETG",
        kind: "PETG",
        color: "#ffffff",
        vendor: "Polymaker",
        notes: null,
      }),
    );
  });
});

describe("MaterialsSection -- edit", () => {
  it("prefills the edit dialog and calls update with changes", async () => {
    setupGet([material()]);
    renderSection();

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Galaxy Black");
    expect(screen.getByLabelText("Kind")).toHaveValue("PLA");

    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "Overture" } });
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledExactlyOnceWith("/materials/1", {
        name: "Galaxy Black",
        kind: "PLA",
        color: "#111111",
        vendor: "Overture",
        notes: null,
      }),
    );
  });
});

describe("MaterialsSection -- error state", () => {
  it("shows a styled error card with Retry when the list query fails, and refetches on click", async () => {
    getMock.mockReset();
    getMock.mockImplementation((path: string) => {
      if (path === "/materials") return Promise.reject(new Error("boom"));
      return Promise.resolve([]);
    });
    renderSection();

    expect(await screen.findByText("Couldn't load materials")).toBeInTheDocument();
    expect(screen.queryByText("not found")).not.toBeInTheDocument();

    getMock.mockImplementation((path: string) => {
      if (path === "/materials") return Promise.resolve([material()]);
      return Promise.resolve([]);
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Galaxy Black")).toBeInTheDocument();
  });
});

describe("MaterialsSection -- delete", () => {
  it("requires confirmation and warns about prints losing their material reference", async () => {
    setupGet([material({ print_count: 3 })]);
    renderSection();

    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(deleteMock).not.toHaveBeenCalled();

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("3 prints will lose their material reference.")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteMock).toHaveBeenCalledExactlyOnceWith("/materials/1"));
  });

  it("shows a no-prints message when print_count is 0", async () => {
    setupGet([material({ print_count: 0 })]);
    renderSection();

    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("This material has no prints logged against it.")).toBeInTheDocument();
  });
});
