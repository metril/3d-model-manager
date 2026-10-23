import { ArrowUpCircleIcon } from "lucide-react";

import { useUpdateInfo } from "@/api/ops";

/** Small "update available" link for the top bar; renders nothing otherwise. */
export function UpdateBadge() {
  const u = useUpdateInfo();
  if (!u.data?.update_available) return null;
  return (
    <a
      href={u.data.url ?? "#"}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary"
      title={`Update available: ${u.data.latest ?? ""}`}
    >
      <ArrowUpCircleIcon className="size-3.5" />
      Update {u.data.latest}
    </a>
  );
}
