/** Suppliers tab (T5): table + create/edit dialog + delete confirm. */
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useCreateSupplier, useDeleteSupplier, usePatchSupplier, useSuppliers } from "@/api/spoolAdmin";
import type { Supplier } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";

function SupplierDialog({ open, onOpenChange, supplier }: { open: boolean; onOpenChange: (o: boolean) => void; supplier?: Supplier }) {
  const { t } = useTranslation();
  const create = useCreateSupplier();
  const patch = usePatchSupplier();
  const mutation = supplier ? patch : create;
  const [f, setF] = useState({ name: "", website: "", customer_number: "", note: "" });

  useEffect(() => {
    if (open) {
      setF({
        name: supplier?.name ?? "",
        website: supplier?.website ?? "",
        customer_number: supplier?.customer_number ?? "",
        note: supplier?.note ?? "",
      });
      create.reset();
      patch.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const payload = {
      name: f.name.trim(),
      website: f.website.trim() || null,
      customer_number: f.customer_number.trim() || null,
      note: f.note.trim() || null,
    };
    const done = { onSuccess: () => onOpenChange(false) };
    if (supplier) patch.mutate({ id: supplier.id, payload }, done);
    else create.mutate(payload, done);
  }
  const bind = (k: keyof typeof f) => ({
    value: f[k],
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF((s) => ({ ...s, [k]: e.target.value })),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="flex flex-col gap-3">
          <DialogHeader>
            <DialogTitle>{supplier ? t("spoolAdmin.sup.editTitle") : t("spoolAdmin.sup.newTitle")}</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sup-name">{t("spoolAdmin.common.name")}</Label>
            <Input id="sup-name" required {...bind("name")} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sup-web">{t("spoolAdmin.sup.website")}</Label>
            <Input id="sup-web" placeholder="https://" {...bind("website")} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sup-cust">{t("spoolAdmin.sup.customerNumber")}</Label>
            <Input id="sup-cust" {...bind("customer_number")} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sup-note">{t("spoolAdmin.sup.note")}</Label>
            <Textarea id="sup-note" {...bind("note")} />
          </div>
          {mutation.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {mutation.error instanceof ApiError ? mutation.error.detail : t("spoolAdmin.common.saveFailed")}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending || !f.name.trim()}>
              {mutation.isPending ? t("spoolAdmin.common.saving") : t("spoolAdmin.common.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function SuppliersPanel() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const canWrite = can("inventory:write");
  const suppliers = useSuppliers();
  const del = useDeleteSupplier();
  const [editing, setEditing] = useState<Supplier | undefined>();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState<Supplier | null>(null);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{t("spoolAdmin.sup.desc")}</p>
        {canWrite ? (
          <Button
            type="button"
            size="sm"
            onClick={() => {
              setEditing(undefined);
              setDialogOpen(true);
            }}
          >
            {t("spoolAdmin.sup.add")}
          </Button>
        ) : null}
      </div>
      {suppliers.isLoading ? (
        <Skeleton className="h-24 w-full rounded-lg" />
      ) : (suppliers.data ?? []).length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">{t("spoolAdmin.sup.empty")}</p>
      ) : (
        <Table className="data-table">
          <TableHeader>
            <TableRow>
              <TableHead>{t("spoolAdmin.common.name")}</TableHead>
              <TableHead>{t("spoolAdmin.sup.website")}</TableHead>
              <TableHead>{t("spoolAdmin.sup.customerNumber")}</TableHead>
              <TableHead>{t("spoolAdmin.sup.note")}</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(suppliers.data ?? []).map((s) => (
              <TableRow key={s.id}>
                <TableCell className="font-medium">{s.name}</TableCell>
                <TableCell>
                  {s.website ? (
                    <a href={s.website} target="_blank" rel="noreferrer noopener" className="text-primary hover:underline">
                      {s.website}
                    </a>
                  ) : (
                    "—"
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs">{s.customer_number ?? "—"}</TableCell>
                <TableCell className="text-muted-foreground">{s.note ?? "—"}</TableCell>
                <TableCell className="text-right">
                  {canWrite ? (
                    <div className="flex justify-end gap-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-label={t("spoolAdmin.common.editNamed", { name: s.name })}
                        onClick={() => {
                          setEditing(s);
                          setDialogOpen(true);
                        }}
                      >
                        {t("spoolAdmin.common.edit")}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-label={t("spoolAdmin.common.deleteNamed", { name: s.name })}
                        onClick={() => setDeleting(s)}
                      >
                        {t("spoolAdmin.common.delete")}
                      </Button>
                    </div>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <SupplierDialog open={dialogOpen} onOpenChange={setDialogOpen} supplier={editing} />
      <ConfirmDialog
        open={deleting != null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={t("spoolAdmin.sup.deleteTitle", { name: deleting?.name ?? "" })}
        description={t("spoolAdmin.sup.deleteDesc")}
        confirmLabel={t("spoolAdmin.common.delete")}
        destructive
        onConfirm={() => {
          if (deleting)
            del.mutate(deleting.id, {
              onSuccess: () => toast.success(t("spoolAdmin.common.deleted")),
              onError: (e) => toast.error(e instanceof ApiError ? e.detail : t("spoolAdmin.common.deleteFailed")),
            });
        }}
      />
    </div>
  );
}
