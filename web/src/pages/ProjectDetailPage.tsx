import { useRef, useState } from "react";
import { EntityTags } from "@/components/tags/EntityTags";
import { TagFilter } from "@/components/tags/TagFilter";
import { EMPTY_TAG_FILTER, matchesTagFilter, type TagFilterValue } from "@/components/tags/tagFilter";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { DownloadIcon, ListPlusIcon, Trash2Icon, UploadIcon } from "lucide-react";
import { toast } from "sonner";

import { useArchives } from "@/api/archives";
import { useAuth } from "@/api/auth";
import { useModelSearchQuery } from "@/api/library";
import {
  PROJECT_STATUSES,
  statusLabel,
  attachmentUrl,
  exportUrl,
  useAddItem,
  useCreateTemplate,
  useDeleteAttachment,
  useDeleteItem,
  useDeleteProject,
  useLinkArchives,
  usePatchItem,
  usePatchProject,
  useProject,
  useProjectArchives,
  useProjectTimeline,
  useQueueRemaining,
  useUploadAttachment,
  type ProjectDetail,
  type ProjectItem,
  type ProjectStatus,
} from "@/api/projects";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { FormDialog } from "@/components/ui/form-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { PageContainer } from "@/components/ui/page-container";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { formatCost, formatDateTime, humanizeBytes, humanizeDuration, useDebouncedValue } from "@/lib/format";

function NumberCell({
  value,
  min,
  disabled,
  label,
  onCommit,
}: {
  value: number;
  min: number;
  disabled: boolean;
  label: string;
  onCommit: (n: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <Input
      type="number"
      min={min}
      className="h-8 w-20"
      aria-label={label}
      disabled={disabled}
      value={draft ?? String(value)}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const n = Number(draft);
        if (draft !== null && Number.isInteger(n) && n >= min && n !== value) onCommit(n);
        setDraft(null);
      }}
    />
  );
}

function AddPartDialog({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const [q, setQ] = useState("");
  const dq = useDebouncedValue(q, 250);
  const results = useModelSearchQuery(dq);
  const add = useAddItem(projectId);
  const models = results.data?.items ?? [];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add part</DialogTitle>
        </DialogHeader>
        <Input
          autoFocus
          aria-label="Search models"
          placeholder="Search models…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <ul className="max-h-64 space-y-1 overflow-auto">
          {models.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                className="w-full rounded px-2 py-1 text-left text-sm hover:bg-muted"
                disabled={add.isPending}
                onClick={() =>
                  add.mutate({ kind: "part", model_id: m.id, quantity: 1 }, { onSuccess: () => onOpenChange(false) })
                }
              >
                {m.name}
              </button>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}

function AddPurchasedDialog({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [qty, setQty] = useState("1");
  const [cost, setCost] = useState("");
  const [url, setUrl] = useState("");
  const add = useAddItem(projectId);
  return (
    <FormDialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          setName("");
          setQty("1");
          setCost("");
          setUrl("");
        }
      }}
      dirty={name !== "" || qty !== "1" || cost !== "" || url !== ""}
      busy={add.isPending}
    >
      <DialogHeader>
        <DialogTitle>Add purchased item</DialogTitle>
      </DialogHeader>
      <Input
        aria-label="Item name"
        placeholder="Name (e.g. M3 screws)"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <div className="flex gap-2">
        <Input aria-label="Quantity" type="number" min={1} value={qty} onChange={(e) => setQty(e.target.value)} />
        <Input
          aria-label="Unit cost"
          type="number"
          min={0}
          step="0.01"
          placeholder="Unit cost"
          value={cost}
          onChange={(e) => setCost(e.target.value)}
        />
      </div>
      <Input aria-label="Link" placeholder="Link (optional)" value={url} onChange={(e) => setUrl(e.target.value)} />
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="outline">
            Cancel
          </Button>
        </DialogClose>
        <Button
          type="button"
          disabled={!name.trim() || add.isPending}
          onClick={() =>
            add.mutate(
              {
                kind: "purchased",
                name: name.trim(),
                quantity: Math.max(1, Number(qty) || 1),
                unit_cost: cost === "" ? null : Number(cost),
                url: url.trim() || null,
              },
              {
                onSuccess: () => {
                  onOpenChange(false);
                  setName("");
                  setCost("");
                  setUrl("");
                },
              },
            )
          }
        >
          Add
        </Button>
      </DialogFooter>
    </FormDialog>
  );
}

function BomCard({ project, canWrite }: { project: ProjectDetail; canWrite: boolean }) {
  const patch = usePatchItem(project.id);
  const del = useDeleteItem(project.id);
  const [partOpen, setPartOpen] = useState(false);
  const [buyOpen, setBuyOpen] = useState(false);
  const edit = (item: ProjectItem, body: Parameters<typeof patch.mutate>[0]["body"]) =>
    patch.mutate({ itemId: item.id, body });
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Bill of materials</CardTitle>
        {canWrite && (
          <div className="flex gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => setPartOpen(true)}>
              Add part
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={() => setBuyOpen(true)}>
              Add purchased item
            </Button>
          </div>
        )}
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {project.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No items yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-1">Item</th>
                <th>Qty</th>
                <th>Progress</th>
                <th>Cost</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {project.items.map((i) => (
                <tr key={i.id} className="border-t">
                  <td className="py-1.5">
                    {i.kind === "part" && i.model_slug ? (
                      <Link to="/models/$slug" params={{ slug: i.model_slug }} className="hover:underline">
                        {i.name}
                      </Link>
                    ) : i.url ? (
                      <a href={i.url} target="_blank" rel="noreferrer" className="hover:underline">
                        {i.name}
                      </a>
                    ) : (
                      i.name
                    )}
                    <Badge variant="outline" className="ml-2">
                      {i.kind}
                    </Badge>
                    {i.file_name && <span className="ml-2 text-xs text-muted-foreground">{i.file_name}</span>}
                    {i.plate && <span className="ml-1 text-xs text-muted-foreground">plate {i.plate}</span>}
                  </td>
                  <td>
                    <NumberCell
                      value={i.quantity}
                      min={1}
                      label={`Quantity for ${i.name}`}
                      disabled={!canWrite}
                      onCommit={(n) => edit(i, { quantity: n })}
                    />
                  </td>
                  <td>
                    {i.kind === "part" ? (
                      <span>
                        {i.done}/{i.quantity}
                        {i.queued > 0 && <span className="text-muted-foreground"> (+{i.queued} queued)</span>}
                      </span>
                    ) : (
                      <NumberCell
                        value={i.acquired}
                        min={0}
                        label={`Acquired for ${i.name}`}
                        disabled={!canWrite}
                        onCommit={(n) => edit(i, { acquired: n })}
                      />
                    )}
                  </td>
                  <td>{i.kind === "purchased" ? formatCost(i.line_cost) : ""}</td>
                  <td className="text-right">
                    {canWrite && (
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        aria-label={`Remove ${i.name}`}
                        onClick={() => del.mutate(i.id)}
                      >
                        <Trash2Icon />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
      <AddPartDialog projectId={project.id} open={partOpen} onOpenChange={setPartOpen} />
      <AddPurchasedDialog projectId={project.id} open={buyOpen} onOpenChange={setBuyOpen} />
    </Card>
  );
}

function ArchivesCard({ projectId, canWrite }: { projectId: number; canWrite: boolean }) {
  const linked = useProjectArchives(projectId);
  const recent = useArchives({ limit: 50 });
  const link = useLinkArchives(projectId);
  const [pick, setPick] = useState("");
  const linkedIds = new Set((linked.data ?? []).map((a) => a.id));
  const options = (recent.data ?? []).filter((a) => !linkedIds.has(a.id));
  return (
    <Card>
      <CardHeader>
        <CardTitle>Print runs</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {(linked.data ?? []).length === 0 && <p className="text-muted-foreground">No runs counted yet.</p>}
        {(linked.data ?? []).map((a) => (
          <div key={a.id} className="flex items-center justify-between gap-2">
            <span>
              #{a.id} {a.model_name ?? "unknown"} · {a.result}
              {a.duration_s ? ` · ${humanizeDuration(a.duration_s)}` : ""}
              {a.total_cost != null ? ` · ${formatCost(a.total_cost)}` : ""}
              {!a.explicit && <span className="text-muted-foreground"> (auto-matched)</span>}
            </span>
            {canWrite && a.explicit && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => link.mutate({ ids: [a.id], unlink: true })}
              >
                Unlink
              </Button>
            )}
          </div>
        ))}
        {canWrite && options.length > 0 && (
          <div className="flex gap-2 pt-2">
            <select
              aria-label="Archive to link"
              className="h-8 flex-1 rounded-md border bg-background px-2 text-sm"
              value={pick}
              onChange={(e) => setPick(e.target.value)}
            >
              <option value="">Link an archived run…</option>
              {options.map((a) => (
                <option key={a.id} value={String(a.id)}>
                  #{a.id} {a.model_name ?? a.subtask_name ?? "run"} ({a.result})
                </option>
              ))}
            </select>
            <Button
              type="button"
              size="sm"
              disabled={!pick}
              onClick={() => link.mutate({ ids: [Number(pick)], unlink: false }, { onSuccess: () => setPick("") })}
            >
              Link
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AttachmentsCard({ project, canWrite }: { project: ProjectDetail; canWrite: boolean }) {
  const upload = useUploadAttachment(project.id);
  const del = useDeleteAttachment(project.id);
  const input = useRef<HTMLInputElement>(null);
  const [tagFilter, setTagFilter] = useState<TagFilterValue>(EMPTY_TAG_FILTER);
  const shown = project.attachments.filter((a) => matchesTagFilter(a.tag_refs, tagFilter));
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Attachments</CardTitle>
        {project.attachments.length > 0 && (
          <TagFilter value={tagFilter} onChange={setTagFilter} countKey="attachments" />
        )}
        {canWrite && (
          <>
            <input
              ref={input}
              type="file"
              hidden
              aria-label="Upload attachment"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload.mutate(f);
                e.target.value = "";
              }}
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={upload.isPending}
              onClick={() => input.current?.click()}
            >
              <UploadIcon /> Upload
            </Button>
          </>
        )}
      </CardHeader>
      <CardContent className="space-y-1 text-sm">
        {project.attachments.length === 0 && <p className="text-muted-foreground">No attachments.</p>}
        {project.attachments.length > 0 && shown.length === 0 && (
          <p className="text-muted-foreground">No attachments match this tag filter.</p>
        )}
        {shown.map((a) => (
          <div key={a.id} className="flex items-center justify-between gap-2">
            <div className="min-w-0 space-y-1">
              <a href={attachmentUrl(project.id, a.id)} download className="hover:underline">
                {a.filename} <span className="text-muted-foreground">({humanizeBytes(a.size)})</span>
              </a>
              <EntityTags
                target={{ kind: "attachment", projectId: project.id, id: a.id }}
                tags={a.tag_refs}
                readOnly={!canWrite}
              />
            </div>
            {canWrite && (
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label={`Delete ${a.filename}`}
                onClick={() => del.mutate(a.id)}
              >
                <Trash2Icon />
              </Button>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function TimelineCard({ projectId }: { projectId: number }) {
  const tl = useProjectTimeline(projectId);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Timeline</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 text-sm">
        {(tl.data ?? []).map((e, idx) => (
          <div key={idx} className="flex gap-3">
            <span className="w-40 shrink-0 text-muted-foreground">{formatDateTime(e.at)}</span>
            <span>{e.message}</span>
          </div>
        ))}
        {(tl.data ?? []).length === 0 && <p className="text-muted-foreground">Nothing yet.</p>}
      </CardContent>
    </Card>
  );
}

function Header({ project, canWrite, canPrint }: { project: ProjectDetail; canWrite: boolean; canPrint: boolean }) {
  const navigate = useNavigate();
  const patch = usePatchProject(project.id);
  const del = useDeleteProject();
  const queue = useQueueRemaining(project.id);
  const makeTemplate = useCreateTemplate(project.id);
  const [delOpen, setDelOpen] = useState(false);
  const [tplOpen, setTplOpen] = useState(false);
  const [tplName, setTplName] = useState("");
  const outstanding = project.items.reduce((n, i) => n + i.outstanding, 0);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
          {project.is_template && <Badge>template</Badge>}
        </div>
        <div className="flex flex-wrap gap-2">
          {canPrint && !project.is_template && (
            <Button
              type="button"
              disabled={queue.isPending || outstanding === 0}
              onClick={() =>
                queue.mutate(undefined, {
                  onSuccess: (r) => {
                    const n = r.enqueued.reduce((s, e) => s + e.copies, 0);
                    toast.success(
                      `Queued ${n} print${n === 1 ? "" : "s"}${r.skipped.length ? `, skipped ${r.skipped.length}` : ""}`,
                    );
                    r.skipped.forEach((s) => toast.warning(`${s.model}: ${s.reason}`));
                  },
                })
              }
            >
              <ListPlusIcon /> Queue remaining ({outstanding})
            </Button>
          )}
          <details className="relative">
            <summary className="inline-flex h-9 cursor-pointer list-none items-center gap-1 rounded-md border px-3 text-sm">
              <DownloadIcon className="size-4" /> Export
            </summary>
            <div className="absolute right-0 z-10 mt-1 flex w-28 flex-col rounded-md border bg-popover p-1 text-sm shadow">
              {(["csv", "json", "zip"] as const).map((f) => (
                <a key={f} href={exportUrl(project.id, f)} download className="rounded px-2 py-1 hover:bg-muted">
                  {f.toUpperCase()}
                </a>
              ))}
            </div>
          </details>
          {canWrite && (
            <>
              <Button type="button" variant="outline" onClick={() => setTplOpen(true)}>
                Save as template
              </Button>
              <Button type="button" variant="outline" onClick={() => setDelOpen(true)}>
                <Trash2Icon /> Delete
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select
          aria-label="Status"
          className="h-8 rounded-md border bg-background px-2 text-sm"
          value={project.status}
          disabled={!canWrite}
          onChange={(e) => patch.mutate({ status: e.target.value as ProjectStatus })}
        >
          {PROJECT_STATUSES.map((s) => (
            <option key={s} value={s}>
              {statusLabel(s)}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1 text-sm">
          Due
          <Input
            type="date"
            className="h-8 w-40"
            aria-label="Due date"
            disabled={!canWrite}
            value={project.due_date ?? ""}
            onChange={(e) => patch.mutate({ due_date: e.target.value || null })}
          />
        </label>
        {project.overdue && <Badge variant="destructive">Overdue</Badge>}
        <EntityTags target={{ kind: "project", id: project.id }} tags={project.tag_refs} readOnly={!canWrite} />
      </div>

      <Textarea
        aria-label="Notes"
        placeholder="Notes"
        defaultValue={project.notes}
        disabled={!canWrite}
        onBlur={(e) => e.target.value !== project.notes && patch.mutate({ notes: e.target.value })}
      />

      <ConfirmDialog
        open={delOpen}
        onOpenChange={setDelOpen}
        title={`Delete project "${project.name}"?`}
        description={`Removes the project, its ${project.items.length} BOM item(s) and ${project.attachments.length} attachment(s). Models and print runs are kept.`}
        confirmLabel="Delete project"
        destructive
        onConfirm={() =>
          del.mutate(project.id, {
            onSuccess: () => void navigate({ to: "/projects" }),
          })
        }
      />
      <FormDialog
        open={tplOpen}
        onOpenChange={(next) => {
          setTplOpen(next);
          if (!next) setTplName("");
        }}
        dirty={tplName !== ""}
        busy={makeTemplate.isPending}
      >
        <DialogHeader>
          <DialogTitle>Save as template</DialogTitle>
        </DialogHeader>
        <Input
          aria-label="Template name"
          placeholder="Template name"
          value={tplName}
          onChange={(e) => setTplName(e.target.value)}
        />
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline">
              Cancel
            </Button>
          </DialogClose>
          <Button
            type="button"
            disabled={!tplName.trim() || makeTemplate.isPending}
            onClick={() =>
              makeTemplate.mutate(tplName.trim(), {
                onSuccess: () => {
                  toast.success("Template saved");
                  setTplOpen(false);
                },
              })
            }
          >
            Save
          </Button>
        </DialogFooter>
      </FormDialog>
    </div>
  );
}

export function ProjectDetailPage() {
  const { id } = useParams({ strict: false }) as { id: string };
  const auth = useAuth();
  const project = useProject(Number(id));

  if (project.isLoading) return <Skeleton className="h-64 w-full" />;
  if (!project.data) return <p className="text-sm text-muted-foreground">Project not found.</p>;
  const p = project.data;
  const canWrite = auth.can("library:write");

  return (
    <PageContainer width="default">
      <Header project={p} canWrite={canWrite} canPrint={auth.can("printers:print")} />
      <Card>
        <CardContent className="grid gap-4 pt-6 sm:grid-cols-2">
          <div className="space-y-2">
            <Progress value={p.progress.percent} aria-label={`${p.progress.percent}% complete`} />
            <p className="text-sm">
              {p.progress.percent}% · {p.progress.parts_done}/{p.progress.parts_total} parts printed
              {p.progress.purchased_total > 0 &&
                ` · ${p.progress.purchased_acquired}/${p.progress.purchased_total} items acquired`}
            </p>
          </div>
          <dl className="grid grid-cols-2 gap-x-4 text-sm">
            <dt className="text-muted-foreground">Filament</dt>
            <dd>{p.cost.filament_g} g</dd>
            <dt className="text-muted-foreground">Print time</dt>
            <dd>{humanizeDuration(p.cost.print_time_s)}</dd>
            <dt className="text-muted-foreground">Print cost</dt>
            <dd>{formatCost(p.cost.print_cost)}</dd>
            <dt className="text-muted-foreground">Purchased</dt>
            <dd>{formatCost(p.cost.purchased_cost)}</dd>
            <dt className="font-medium">Total</dt>
            <dd className="font-medium">{formatCost(p.cost.total_cost)}</dd>
            <dt className="text-muted-foreground">Est. remaining</dt>
            <dd>{formatCost(p.cost.estimated_remaining_cost)}</dd>
          </dl>
        </CardContent>
      </Card>
      <BomCard project={p} canWrite={canWrite} />
      <ArchivesCard projectId={p.id} canWrite={canWrite} />
      <AttachmentsCard project={p} canWrite={canWrite} />
      <TimelineCard projectId={p.id} />
    </PageContainer>
  );
}
