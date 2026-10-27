import { useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { PlusIcon } from "lucide-react";

import { useAuth } from "@/api/auth";
import {
  PROJECT_STATUSES,
  statusLabel,
  useCreateProject,
  useFromTemplate,
  useProjects,
  useProjectTemplates,
  type ProjectStatus,
  type ProjectSummary,
} from "@/api/projects";
import { useModel } from "@/api/library";
import { TagChips } from "@/components/tags/TagChip";
import { tagRefsOf } from "@/components/tags/tagRefs";
import { TagFilter } from "@/components/tags/TagFilter";
import { EMPTY_TAG_FILTER, type TagFilterValue } from "@/components/tags/tagFilter";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { PageContainer } from "@/components/ui/page-container";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { formatCost, formatDate, useDebouncedValue } from "@/lib/format";


export function ProjectCover({ slug }: { slug: string | null }) {
  const model = useModel(slug ?? "");
  const hash = slug ? model.data?.cover_blob_hash : null;
  if (!hash) return <div className="size-full bg-muted" />;
  return (
    <img
      src={`/api/blobs/${hash}/thumb?size=256`}
      alt=""
      loading="lazy"
      className="size-full object-cover"
    />
  );
}

function ProjectCard({ project, onUse }: { project: ProjectSummary; onUse?: () => void }) {
  const p = project.progress;
  return (
    <Card className="overflow-hidden py-0">
      <div className="h-28">
        <ProjectCover slug={project.cover_model_slug} />
      </div>
      <CardContent className="space-y-2 pb-4">
        <div className="flex items-start justify-between gap-2">
          {onUse ? (
            <span className="font-medium">{project.name}</span>
          ) : (
            <Link
              to="/projects/$id"
              params={{ id: String(project.id) }}
              className="font-medium hover:underline"
            >
              {project.name}
            </Link>
          )}
          {!project.is_template && <Badge variant="secondary">{statusLabel(project.status)}</Badge>}
        </div>
        <TagChips tags={tagRefsOf(project)} />
        {!project.is_template && (
          <>
            <Progress value={p.percent} aria-label={`${p.percent}% complete`} />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>
                {p.parts_done}/{p.parts_total} parts · {p.percent}%
              </span>
              <span>{formatCost(project.cost.total_cost)}</span>
            </div>
          </>
        )}
        {project.due_date && (
          <div className={project.overdue ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
            {project.overdue ? "Overdue · " : "Due "}
            {formatDate(project.due_date)}
          </div>
        )}
        {onUse && (
          <Button type="button" size="sm" onClick={onUse}>
            Use template
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function NameDialog({
  title,
  open,
  onOpenChange,
  onSubmit,
  pending,
}: {
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (name: string) => void;
  pending: boolean;
}) {
  const [name, setName] = useState("");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <Input
          autoFocus
          aria-label="Project name"
          placeholder="Project name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim()) onSubmit(name.trim());
          }}
        />
        <DialogFooter>
          <Button type="button" disabled={!name.trim() || pending} onClick={() => onSubmit(name.trim())}>
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ProjectsPage() {
  const auth = useAuth();
  const canWrite = auth.can("library:write");
  const navigate = useNavigate();
  const [tab, setTab] = useState<"projects" | "templates">("projects");
  const [status, setStatus] = useState<ProjectStatus | "">("");
  const [search, setSearch] = useState("");
  const q = useDebouncedValue(search, 250);
  const [tagFilter, setTagFilter] = useState<TagFilterValue>(EMPTY_TAG_FILTER);
  const projects = useProjects({
    status: status || undefined,
    q: q || undefined,
    tags: tagFilter.tags.length ? tagFilter.tags : undefined,
    tagMode: tagFilter.tags.length > 1 ? tagFilter.mode : undefined,
  });
  const templates = useProjectTemplates();
  const create = useCreateProject();
  const fromTemplate = useFromTemplate();
  const [createOpen, setCreateOpen] = useState(false);
  const [templateId, setTemplateId] = useState<number | null>(null);

  const goto = (id: number) => void navigate({ to: "/projects/$id", params: { id: String(id) } });
  const list = tab === "projects" ? projects : templates;

  return (
    <PageContainer width="default">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold">Projects</h1>
        {canWrite && (
          <Button type="button" onClick={() => setCreateOpen(true)}>
            <PlusIcon /> New project
          </Button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant={tab === "projects" ? "default" : "outline"}
          onClick={() => setTab("projects")}
        >
          Projects
        </Button>
        <Button
          type="button"
          size="sm"
          variant={tab === "templates" ? "default" : "outline"}
          onClick={() => setTab("templates")}
        >
          Templates
        </Button>
        {tab === "projects" && (
          <>
            <select
              aria-label="Status filter"
              className="h-8 rounded-md border bg-background px-2 text-sm"
              value={status}
              onChange={(e) => setStatus(e.target.value as ProjectStatus | "")}
            >
              <option value="">All statuses</option>
              {PROJECT_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {statusLabel(s)}
                </option>
              ))}
            </select>
            <Input
              className="h-8 w-56"
              aria-label="Search projects"
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <TagFilter value={tagFilter} onChange={setTagFilter} countKey="projects" />
          </>
        )}
      </div>

      {list.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : (list.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {tab === "projects"
            ? "No projects yet. Create one, or add models to a project from the library."
            : "No templates yet. Save a project as a template from its page."}
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(list.data ?? []).map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              onUse={tab === "templates" && canWrite ? () => setTemplateId(p.id) : undefined}
            />
          ))}
        </div>
      )}

      <NameDialog
        title="New project"
        open={createOpen}
        onOpenChange={setCreateOpen}
        pending={create.isPending}
        onSubmit={(name) => create.mutate({ name }, { onSuccess: (p) => goto(p.id) })}
      />
      <NameDialog
        title="Create project from template"
        open={templateId !== null}
        onOpenChange={(o) => !o && setTemplateId(null)}
        pending={fromTemplate.isPending}
        onSubmit={(name) =>
          templateId !== null &&
          fromTemplate.mutate({ templateId, name }, { onSuccess: (p) => goto(p.id) })
        }
      />
    </PageContainer>
  );
}
