/**
 * Categories CRUD (R13b, Settings -> General): name + a color swatch,
 * mirroring `StorageBackendsCard`'s add/edit-dialog + table-row conventions.
 * A category's `color` is a `TagColor` palette name -- the SAME fixed
 * 10-key palette tags use, edited with the identical swatch-grid picker
 * pattern as `TagEditor`'s `TagColorPicker` (below), not a free-form hex
 * input.
 */
import { useState, type FormEvent } from "react";

import { useCategories, useCreateCategory, useDeleteCategory, useUpdateCategory } from "@/api/categories";
import { ApiError } from "@/api/client";
import type { CategoryOut, TagColor } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useDiscardGuard } from "@/components/ui/use-discard-guard";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { TAG_COLORS, tagColorClass, tagSwatchClass } from "@/lib/tagColors";
import { cn } from "@/lib/utils";

const DEFAULT_COLOR: TagColor = "slate";

/** Swatch-grid color picker, same pattern as `TagEditor`'s `TagColorPicker`
 * -- a 10-key palette grid in a popover, triggered by a small solid swatch
 * button rather than the tag name itself (categories aren't inline-editable
 * text the way a tag chip is). */
function CategoryColorPicker({ color, onPick }: { color: TagColor; onPick: (color: TagColor) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Category color"
          className={cn("size-6 shrink-0 rounded-full ring-offset-2 ring-offset-background", tagSwatchClass(color))}
        />
      </PopoverTrigger>
      <PopoverContent className="w-auto p-2" align="start">
        <div className="grid grid-cols-5 gap-1.5">
          {TAG_COLORS.map((option) => (
            <button
              key={option}
              type="button"
              aria-label={`Color ${option}`}
              aria-pressed={color === option}
              className={cn(
                "size-5 rounded-full ring-offset-2 ring-offset-background",
                tagSwatchClass(option),
                color === option && "ring-2 ring-foreground",
              )}
              onClick={() => {
                onPick(option);
                setOpen(false);
              }}
            />
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function CategoryFormDialog({ trigger, category }: { trigger: React.ReactNode; category?: CategoryOut }) {
  const isEdit = category !== undefined;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(category?.name ?? "");
  const [color, setColor] = useState<TagColor>(category?.color ?? DEFAULT_COLOR);

  const createCategory = useCreateCategory();
  const updateCategory = useUpdateCategory();
  const mutation = isEdit ? updateCategory : createCategory;

  const dirty = JSON.stringify([name, color]) !== JSON.stringify([category?.name ?? "", category?.color ?? DEFAULT_COLOR]);

  function reset() {
    setName(category?.name ?? "");
    setColor(category?.color ?? DEFAULT_COLOR);
    createCategory.reset();
    updateCategory.reset();
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    if (isEdit) {
      updateCategory.mutate({ id: category.id, payload: { name: trimmed, color } }, { onSuccess: () => setOpen(false) });
    } else {
      createCategory.mutate({ name: trimmed, color }, { onSuccess: () => { setOpen(false); reset(); } });
    }
  }

  const { requestChange, confirm } = useDiscardGuard({
    onOpenChange: (next) => {
      setOpen(next);
      if (!next) reset();
    },
    dirty,
    busy: mutation.isPending,
  });

  return (
    <>
    <Dialog open={open} onOpenChange={requestChange}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent dismissable={false}>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{isEdit ? `Edit "${category.name}"` : "Add category"}</DialogTitle>
            <DialogDescription>
              A model has at most one category -- a coarser grouping than tags.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="category-name">Name</Label>
              <Input
                id="category-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={mutation.isPending}
                required
                autoFocus
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Color</Label>
              <CategoryColorPicker color={color} onPick={setColor} />
            </div>
            {mutation.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {mutation.error instanceof ApiError ? mutation.error.detail : "Could not save this category"}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={name.trim().length === 0 || mutation.isPending}>
              {mutation.isPending ? "Saving..." : isEdit ? "Save changes" : "Add category"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
    {confirm}
    </>
  );
}

function CategoryRow({ category }: { category: CategoryOut }) {
  const deleteCategory = useDeleteCategory();
  const deletingThis = deleteCategory.isPending && deleteCategory.variables === category.id;
  const deleteErrorForThis = deleteCategory.isError && deleteCategory.variables === category.id;

  return (
    <TableRow>
      <TableCell>
        <span
          aria-hidden="true"
          className={cn("inline-block size-3 rounded-full align-middle", tagColorClass(category.color) ?? "bg-muted-foreground")}
        />
      </TableCell>
      <TableCell className="font-medium text-foreground">{category.name}</TableCell>
      <TableCell className="text-muted-foreground">{category.model_count}</TableCell>
      <TableCell className="whitespace-normal">
        <div className="flex flex-wrap items-center gap-1.5">
          <CategoryFormDialog
            category={category}
            trigger={
              <Button type="button" size="sm" variant="outline">
                Edit
              </Button>
            }
          />
          <ConfirmDialog
            trigger={
              <Button type="button" size="sm" variant="destructive" disabled={deletingThis}>
                {deletingThis ? "Deleting..." : "Delete"}
              </Button>
            }
            title={`Delete "${category.name}"?`}
            description={
              category.model_count > 0
                ? `${category.model_count} model${category.model_count === 1 ? "" : "s"} will become uncategorized.`
                : "This category has no models on it."
            }
            confirmLabel="Delete"
            destructive
            onConfirm={() => deleteCategory.mutate(category.id)}
          />
        </div>
        {deleteErrorForThis ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {deleteCategory.error instanceof ApiError ? deleteCategory.error.detail : "Could not delete this category"}
          </p>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function CategoriesSection() {
  const categoriesQuery = useCategories();
  const categories = categoriesQuery.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Categories</CardTitle>
        <CardDescription>
          Exclusive, single-per-model groupings (unlike tags, which can be many-per-model).
        </CardDescription>
        <CardAction>
          <CategoryFormDialog
            trigger={
              <Button type="button" variant="outline">
                Add category
              </Button>
            }
          />
        </CardAction>
      </CardHeader>
      <CardContent>
        {categoriesQuery.isLoading ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : categoriesQuery.isError ? (
          <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-8 text-center">
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">Couldn&apos;t load categories</p>
              <p role="alert" className="text-sm text-muted-foreground">
                {categoriesQuery.error instanceof ApiError ? categoriesQuery.error.detail : "Something went wrong."}
              </p>
            </div>
            <Button type="button" size="sm" onClick={() => void categoriesQuery.refetch()}>
              Retry
            </Button>
          </div>
        ) : categories.length === 0 ? (
          <p className="text-sm text-muted-foreground">No categories yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead />
                <TableHead>Name</TableHead>
                <TableHead>Models</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {categories.map((category) => (
                <CategoryRow key={category.id} category={category} />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
