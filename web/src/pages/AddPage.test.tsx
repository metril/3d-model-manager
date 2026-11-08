import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AddPage } from "@/pages/AddPage";

const { searchBox, importBox, createImportMock, createModelMock, fetchNextPageMock } = vi.hoisted(
  () => ({
    searchBox: {
      current: {
        data: undefined as unknown,
        isLoading: false,
        isError: false,
        error: null as unknown,
        hasNextPage: false,
        isFetchingNextPage: false,
        fetchNextPage: () => {},
      },
    },
    importBox: { current: { data: undefined as unknown } },
    createImportMock: vi.fn(),
    createModelMock: vi.fn(),
    fetchNextPageMock: vi.fn(),
  }),
);

vi.mock("@/api/imports", () => ({
  useImportSearch: () => searchBox.current,
  useCreateImport: () => ({ mutate: createImportMock, isPending: false, isError: false, error: null }),
  useImport: () => importBox.current,
}));

vi.mock("@/api/library", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/library")>();
  return { ...actual, useCreateModel: () => ({ mutateAsync: createModelMock }) };
});

// The dropzone's queue/SSE machinery is exercised in UploadDropzone.test.tsx;
// here we only need to drive the panel's `resolveTarget` (model-creation reuse).
vi.mock("@/components/upload/UploadDropzone", () => ({
  UploadDropzone: ({
    resolveTarget,
    disabled,
  }: {
    resolveTarget: () => Promise<unknown>;
    disabled?: boolean;
  }) => (
    <button type="button" disabled={disabled} onClick={() => void resolveTarget()}>
      resolve-target
    </button>
  ),
}));

// Stateful Tabs mock (Radix tab switching is unreliable under jsdom) that
// renders only the active panel and switches on a trigger click.
vi.mock("@/components/ui/tabs", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  const TabsCtx = React.createContext<{ value: string; setValue: (value: string) => void }>({
    value: "",
    setValue: () => {},
  });
  return {
    Tabs: ({ defaultValue, children }: { defaultValue?: string; children?: ReactNode }) => {
      const [value, setValue] = React.useState(defaultValue ?? "");
      return <TabsCtx.Provider value={{ value, setValue }}>{children}</TabsCtx.Provider>;
    },
    TabsList: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    TabsTrigger: ({ value, children }: { value: string; children?: ReactNode }) => {
      const ctx = React.useContext(TabsCtx);
      return (
        <button role="tab" type="button" onClick={() => ctx.setValue(value)}>
          {children}
        </button>
      );
    },
    TabsContent: ({ value, children }: { value: string; children?: ReactNode }) => {
      const ctx = React.useContext(TabsCtx);
      return ctx.value === value ? <div>{children}</div> : null;
    },
  };
});

function renderAddPage() {
  const rootRoute = createRootRoute();
  const addRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: AddPage });
  const settingsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([addRoute, settingsRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  createImportMock.mockReset();
  createModelMock.mockReset();
  fetchNextPageMock.mockReset();
  searchBox.current = {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: fetchNextPageMock,
  };
  importBox.current = { data: undefined };
});

describe("AddPage", () => {
  it("offers Upload / Import URL / Search tabs, with saved collections no longer buried here", async () => {
    renderAddPage();
    expect(await screen.findByRole("tab", { name: "Upload files" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Import from URL" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Search galleries" })).toBeInTheDocument();
    // Saved collections moved out to their own /collections page -- as a tab
    // behind "Add to library" nobody could find them.
    expect(screen.queryByRole("tab", { name: "Saved" })).not.toBeInTheDocument();
  });

  it("creates the new model once and reuses it for a second upload batch", async () => {
    createModelMock.mockResolvedValue({ id: 7, slug: "m", name: "M", current_revision: { id: 70 } });
    renderAddPage();

    fireEvent.change(await screen.findByLabelText("New model name"), { target: { value: "My model" } });
    const resolve = screen.getByRole("button", { name: "resolve-target" });

    fireEvent.click(resolve);
    await waitFor(() => expect(createModelMock).toHaveBeenCalledTimes(1));
    fireEvent.click(resolve);
    // second batch reuses the cached target -- no duplicate model
    await new Promise((r) => setTimeout(r, 0));
    expect(createModelMock).toHaveBeenCalledTimes(1);
  });

  it("imports a pasted, supported URL", async () => {
    renderAddPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Import from URL" }));

    fireEvent.change(screen.getByLabelText("URL"), {
      target: { value: "https://www.printables.com/model/3161" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import" }));

    expect(createImportMock).toHaveBeenCalledWith(
      { url: "https://www.printables.com/model/3161" },
      expect.anything(),
    );
  });

  it("renders federated results with a site badge and a Load more control", async () => {
    searchBox.current = {
      data: {
        pages: [
          {
            results: [
              {
                site: "thingiverse",
                external_id: "1",
                title: "TV One",
                url: "https://www.thingiverse.com/thing:1",
                author: "alice",
                thumbnail_url: null,
              },
              {
                site: "printables",
                external_id: "2",
                title: "PR Two",
                url: "https://www.printables.com/model/2",
                author: null,
                thumbnail_url: null,
              },
            ],
            per_site: [
              { site: "thingiverse", count: 1, has_more: true, status: "ok", detail: null },
              { site: "printables", count: 1, has_more: false, status: "ok", detail: null },
            ],
          },
        ],
      },
      isLoading: false,
      isError: false,
      error: null,
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextPage: fetchNextPageMock,
    };

    renderAddPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Search galleries" }));
    fireEvent.change(screen.getByLabelText("Search query"), { target: { value: "benchy" } });

    const tvCard = (await screen.findByText("TV One")).closest("[data-slot='card']");
    const prCard = screen.getByText("PR Two").closest("[data-slot='card']");
    if (!tvCard || !prCard) throw new Error("result cards not found");
    // per-card site badge (lowercase site value; the toggle chips use labels)
    expect(within(tvCard as HTMLElement).getByText("Thingiverse")).toBeInTheDocument();
    expect(within(prCard as HTMLElement).getByText("Printables")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(fetchNextPageMock).toHaveBeenCalled();
  });
});
