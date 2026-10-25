import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpoolForm } from "@/components/inventory/SpoolForm";

const { getMock, postMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn(), putMock: vi.fn() }));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, put: putMock } };
});
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => true, canPrinter: () => true }) }));
vi.mock("@/components/ui/select", () => import("@/test/selectMock"));

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SpoolForm open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("SpoolForm housekeeping", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    putMock.mockReset();
    getMock.mockImplementation((path: string) => {
      if (path === "/storage-locations") return Promise.resolve([{ id: 1, name: "Shelf A", identifier: null, created_at: "" }]);
      if (path === "/suppliers")
        return Promise.resolve([{ id: 9, name: "Acme", website: null, customer_number: null, note: null, created_at: "" }]);
      if (path.startsWith("/color-catalog/search"))
        return Promise.resolve([
          { id: 5, manufacturer: "Bambu Lab", color_name: "Jade White", hex: "#FFFFFF", material: "PLA", is_default: true, extra_colors: [], effect_type: null },
        ]);
      return Promise.resolve([]);
    });
  });

  it("creates a location inline and submits location_id", async () => {
    postMock.mockImplementation((path: string) =>
      path === "/storage-locations"
        ? Promise.resolve({ id: 42, name: "Dry box", identifier: null, created_at: "" })
        : Promise.resolve({ id: 100 }),
    );
    setup();
    const input = screen.getByLabelText("Location");
    fireEvent.change(input, { target: { value: "Dry box" } });
    fireEvent.click(await screen.findByRole("option", { name: "Create ‘Dry box’" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/storage-locations", { name: "Dry box" }));
    await waitFor(() => expect(input).toHaveValue("Dry box"));

    fireEvent.click(screen.getByRole("button", { name: "Add spool" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/spools", expect.objectContaining({ location_id: 42 })),
    );
  });

  it("fills colour hex, name and brand from a catalog pick", async () => {
    setup();
    fireEvent.change(screen.getByLabelText("Search colour catalog"), { target: { value: "jade" } });
    fireEvent.click(await screen.findByRole("option", { name: /Jade White/ }));
    expect(screen.getByLabelText("Colour")).toHaveValue("#FFFFFF");
    expect(screen.getByLabelText("Colour name")).toHaveValue("Jade White");
    expect(screen.getByLabelText("Brand")).toHaveValue("Bambu Lab");
  });

  it("saves supplier links via PUT after the spool is created", async () => {
    postMock.mockResolvedValue({ id: 100 });
    putMock.mockResolvedValue([]);
    setup();
    const add = screen.getByRole("button", { name: "Add supplier" });
    await waitFor(() => expect(add).toBeEnabled());
    fireEvent.click(add);
    fireEvent.change(screen.getByLabelText("Article no."), { target: { value: "A-1" } });
    fireEvent.change(screen.getByLabelText("Quoted price per kg"), { target: { value: "19.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Add spool" }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/spools/100/suppliers", [
        { supplier_id: 9, article_number: "A-1", quoted_price_per_kg: 19.5, is_purchase_source: true },
      ]),
    );
  });
});
