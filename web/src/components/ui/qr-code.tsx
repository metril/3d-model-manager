import { useMemo } from "react";

import { qrMatrix } from "@/lib/qr";

/** Client-side QR (no dependency): one SVG path of dark modules on a white quiet-zone. */
export function QrCode({ value, label, className }: { value: string; label: string; className?: string }) {
  const m = useMemo(() => {
    try {
      return qrMatrix(value);
    } catch {
      return null; // payload too long: the caller still shows the manual key
    }
  }, [value]);
  const q = 4;
  const n = (m?.length ?? 0) + q * 2;
  const d = useMemo(() => {
    if (!m) return "";
    const parts: string[] = [];
    m.forEach((row, y) =>
      row.forEach((on, x) => {
        if (on) parts.push(`M${x + q} ${y + q}h1v1h-1z`);
      }),
    );
    return parts.join("");
  }, [m]);
  if (!m) return null;
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${n} ${n}`}
      shapeRendering="crispEdges"
      className={className ?? "size-48 rounded-md bg-white"}
    >
      <rect width={n} height={n} fill="#fff" />
      <path d={d} fill="#000" />
    </svg>
  );
}
