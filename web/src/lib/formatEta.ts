/** `mc_remaining_time` (minutes) -> `"1h 05m"` / `"5m"`. Zero-pads minutes
 * only once there's an hour component, so the detail-sheet style doesn't
 * misread a short remaining time as a typo (`"05m"` alone looks wrong). */
export function formatEta(minutes: number | null | undefined): string | null {
  if (minutes == null || !Number.isFinite(minutes) || minutes < 0) return null;
  const total = Math.round(minutes);
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  if (hours <= 0) return `${mins}m`;
  return `${hours}h ${String(mins).padStart(2, "0")}m`;
}
