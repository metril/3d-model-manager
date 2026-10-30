import type { SpoolOut } from "@/api/types";

export function driedDetail(spool: SpoolOut): string | undefined {
  if (!spool.last_dried_at) return undefined;
  const parts = [
    spool.last_dried_temp_c != null ? `${spool.last_dried_temp_c} °C` : null,
    spool.last_dried_hours != null ? `${spool.last_dried_hours} h` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : undefined;
}
