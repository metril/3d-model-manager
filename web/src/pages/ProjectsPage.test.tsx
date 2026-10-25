import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AddToProjectDialog } from "@/components/projects/AddToProjectDialog";
import { ProjectsPage } from "@/pages/ProjectsPage";
import type { ProjectSummary } from "@/api/projects";

const mocks = vi.hoisted(() => ({
  projects: [] as ProjectSummary[],
  templates: [] as ProjectSummary[],
  canWrite: true,
  addMutate: vi.fn(),
  createMutate: vi.fn(),
  lastFilters: undefined as unknown,
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
}));
vi.mock("@/api/auth", () => ({ useAuth: () => ({ can: () => mocks.canWrite }) }));
vi.mock("@/api/library", () => ({ useModel: () => ({ data: undefined }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));
vi.mock("@/api/projects", async () => {
  const actual = await vi.importActual<typeof import("@/api/projects")>("@/api/projects");
  return {
    ...actual,
    useProjects: (f: unknown) => {
      mocks.lastFilters = f;
      return { data: mocks.projects, isLoading: false };
    },
    useProjectTemplates: () => ({ data: mocks.templates, isLoading: false }),
    useCreateProject: () => ({ mutateAsync: vi.fn().mockResolvedValue({ id: 9 }), mutate: mocks.createMutate, isPending: false }),
    useFromTemplate: () => ({ mutate: vi.fn(), isPending: false }),
    useAddModelsToProject: () => ({ mutateAsync: mocks.addMutate, isPending: false }),
  };
});

function project(over: Partial<ProjectSummary>): ProjectSummary {
  return {
    id: 1,
    name: "Robot",
    description: "",
    status: "active",
    due_date: "2020-01-01",
    overdue: true,
    cover_model_id: null,
    cover_model_slug: null,
    tags: [],
    is_template: false,
    completed_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    progress: { parts_total: 4, parts_done: 2, parts_queued: 0, purchased_total: 0, purchased_acquired: 0, percent: 50 },
    cost: { filament_g: 0, print_time_s: 0, print_cost: 0, purchased_cost: 0, total_cost: 12.5, estimated_remaining_cost: 0 },
    ...over,
  };
}

beforeEach(() => {
  mocks.projects = [project({})];
  mocks.templates = [project({ id: 2, name: "Tpl", is_template: true })];
  mocks.canWrite = true;
  mocks.addMutate.mockReset().mockResolvedValue({});
});

describe("ProjectsPage", () => {
  it("renders cards with progress, overdue and cost", () => {
    render(<ProjectsPage />);
    expect(screen.getByText("Robot")).toBeInTheDocument();
    expect(screen.getByText(/2\/4 parts · 50%/)).toBeInTheDocument();
    expect(screen.getByText(/Overdue/)).toBeInTheDocument();
    expect(screen.getByText("12.50")).toBeInTheDocument();
  });

  it("hides New project without library:write", () => {
    mocks.canWrite = false;
    render(<ProjectsPage />);
    expect(screen.queryByRole("button", { name: /New project/ })).toBeNull();
  });

  it("passes the status filter and shows templates tab", () => {
    render(<ProjectsPage />);
    fireEvent.change(screen.getByLabelText("Status filter"), { target: { value: "completed" } });
    expect(mocks.lastFilters).toMatchObject({ status: "completed" });
    fireEvent.click(screen.getByRole("button", { name: "Templates" }));
    expect(screen.getByText("Tpl")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use template" })).toBeInTheDocument();
  });
});

describe("AddToProjectDialog", () => {
  it("adds the models to the chosen project", async () => {
    const onOpenChange = vi.fn();
    render(<AddToProjectDialog open onOpenChange={onOpenChange} modelIds={[5, 6]} />);
    const add = screen.getByRole("button", { name: "Add" });
    expect(add).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "1" } });
    fireEvent.click(add);
    await waitFor(() => expect(mocks.addMutate).toHaveBeenCalledWith({ projectId: 1, modelIds: [5, 6] }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("creates a new project first", async () => {
    render(<AddToProjectDialog open onOpenChange={vi.fn()} modelIds={[5]} />);
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "new" } });
    fireEvent.change(screen.getByLabelText("New project name"), { target: { value: "Fresh" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(mocks.addMutate).toHaveBeenCalledWith({ projectId: 9, modelIds: [5] }));
  });
});
