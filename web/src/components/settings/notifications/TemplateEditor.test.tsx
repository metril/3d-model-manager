import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TemplateEditor } from "@/components/settings/notifications/TemplateEditor";

import { catalog, renderWithQuery } from "./testUtils";

const { getMock, postMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, delete: deleteMock } };
});

describe("TemplateEditor", () => {
  let templates: unknown[];
  beforeEach(() => {
    templates = [];
    for (const m of [getMock, postMock, deleteMock]) m.mockReset();
    getMock.mockImplementation((p: string) => {
      if (p === "/notifications/events") return Promise.resolve(catalog);
      if (p === "/notifications/templates") return Promise.resolve(templates);
      if (p === "/notifications/templates/defaults")
        return Promise.resolve({ print_done: { title_tpl: "Done {{ printer }}", body_tpl: "Body {{ model }}" } });
      return Promise.resolve({});
    });
    postMock.mockImplementation((p: string, b: { title_tpl: string }) =>
      Promise.resolve(p.endsWith("/preview") ? { title: `P:${b.title_tpl}`, body: "pb", error: null } : {}),
    );
  });

  it("loads defaults, lists variables and shows a debounced preview", async () => {
    renderWithQuery(<TemplateEditor />);
    expect(await screen.findByDisplayValue("Done {{ printer }}")).toBeInTheDocument();
    expect(screen.getByText("{{ model }}")).toBeInTheDocument();
    expect(await screen.findByTestId("preview-title")).toHaveTextContent("P:Done {{ printer }}");
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Hi" } });
    await waitFor(() => expect(screen.getByTestId("preview-title")).toHaveTextContent("P:Hi"));
  });

  it("saves a new template via POST", async () => {
    renderWithQuery(<TemplateEditor />);
    await screen.findByDisplayValue("Done {{ printer }}");
    fireEvent.click(screen.getByRole("button", { name: "Save template" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/notifications/templates", {
        event: "print_done",
        title_tpl: "Done {{ printer }}",
        body_tpl: "Body {{ model }}",
      }),
    );
  });

  it("reset deletes the saved override", async () => {
    templates = [{ id: 5, event: "print_done", title_tpl: "Custom", body_tpl: "B" }];
    deleteMock.mockResolvedValue(undefined);
    renderWithQuery(<TemplateEditor />);
    await screen.findByDisplayValue("Custom");
    fireEvent.click(screen.getByRole("button", { name: "Reset to default" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/notifications/templates/5"));
  });
});
