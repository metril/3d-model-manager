/**
 * Categories CRUD (R13b, Settings -> General): name + a color swatch,
 * mirroring `StorageBackendsCard`'s add/edit-dialog + table-row conventions.
 * A category's `color` is a `TagColor` palette name -- the SAME fixed
 * 10-key palette tags use, edited with the identical swatch-grid picker
 * pattern as `TagEditor`'s `TagColorPicker` (below), not a free-form hex
 * input.
 */
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

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
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t("settingsCategories.colorAria")}
          className={cn("size-6 shrink-0 rounded-full ring-offset-2 ring-offset-background", tagSwatchClass(color))}
        />
      </PopoverTrigger>
      <PopoverContent className="w-auto p-2" align="start">
        <div className="grid grid-cols-5 gap-1.5">
          {TAG_COLORS.map((option) => (
            <button
              key={option}
              type="button"
              aria-label={t("settingsCategories.colorOption", { color: option })}
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
  const { t } = useTranslation();
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
      createCategory.mutate({ name: trimmed, color }, { onSuccess: () => setOpen(false) });
    }
  }

  const { requestChange, confirm } = useDiscardGuard({
    onOpenChange: (next) => {
      // Re-snapshot on every open and close: fresh props, no stale secrets.
      reset();
      setOpen(next);
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
            <DialogTitle>{isEdit ? t("settingsCategories.editTitle", { name: category.name }) : t("settingsCategories.addTitle")}</DialogTitle>
            <DialogDescription>
              {t("settingsCategories.dialogDesc")}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="category-name">{t("settingsCategories.name")}</Label>
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
              <Label>{t("settingsCategories.color")}</Label>
              <CategoryColorPicker color={color} onPick={setColor} />
            </div>
            {mutation.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {mutation.error instanceof ApiError ? mutation.error.detail : t("settingsCategories.saveError")}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={name.trim().length === 0 || mutation.isPending}>
              {mutation.isPending ? t("settingsCategories.saving") : isEdit ? t("settingsCategories.saveChanges") : t("settingsCategories.addTitle")}
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
  const { t } = useTranslation();
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
                {t("common.edit")}
              </Button>
            }
          />
          <ConfirmDialog
            trigger={
              <Button type="button" size="sm" variant="destructive" disabled={deletingThis}>
                {deletingThis ? t("settingsCategories.deleting") : t("common.delete")}
              </Button>
            }
            title={t("settingsCategories.deleteTitle", { name: category.name })}
            description={
              category.model_count > 0
                ? t("settingsCategories.deleteDesc", { count: category.model_count })
                : t("settingsCategories.deleteDescNone")
            }
            confirmLabel={t("common.delete")}
            destructive
            onConfirm={() => deleteCategory.mutate(category.id)}
          />
        </div>
        {deleteErrorForThis ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {deleteCategory.error instanceof ApiError ? deleteCategory.error.detail : t("settingsCategories.deleteError")}
          </p>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function CategoriesSection() {
  const { t } = useTranslation();
  const categoriesQuery = useCategories();
  const categories = categoriesQuery.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsCategories.title")}</CardTitle>
        <CardDescription>
          {t("settingsCategories.desc")}
        </CardDescription>
        <CardAction>
          <CategoryFormDialog
            trigger={
              <Button type="button" variant="outline">
                {t("settingsCategories.addTitle")}
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
              <p className="text-sm font-medium text-foreground">{t("settingsCategories.loadError")}</p>
              <p role="alert" className="text-sm text-muted-foreground">
                {categoriesQuery.error instanceof ApiError ? categoriesQuery.error.detail : t("settingsCategories.genericError")}
              </p>
            </div>
            <Button type="button" size="sm" onClick={() => void categoriesQuery.refetch()}>
              {t("common.retry")}
            </Button>
          </div>
        ) : categories.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("settingsCategories.empty")}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead />
                <TableHead>{t("settingsCategories.name")}</TableHead>
                <TableHead>{t("settingsCategories.colModels")}</TableHead>
                <TableHead>{t("settingsCategories.colActions")}</TableHead>
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
