import { FolderInputIcon, MoreHorizontalIcon, PencilIcon, Trash2Icon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

export function ManageMenu({
  label,
  busy,
  onRename,
  onMove,
  onDelete,
}: {
  label: string;
  busy?: boolean;
  onRename: () => void;
  onMove: () => void;
  onDelete?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="ghost" size="icon-sm" aria-label={t("explorer.manage.manage", { name: label })}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={onRename}>
          <PencilIcon className="size-4" />
          {t("explorer.manage.rename")}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onMove}>
          <FolderInputIcon className="size-4" />
          {t("explorer.manage.move")}
        </DropdownMenuItem>
        {onDelete ? (
          <DropdownMenuItem variant="destructive" disabled={busy} onSelect={onDelete}>
            <Trash2Icon className="size-4" />
            {t("explorer.manage.delete")}
          </DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
