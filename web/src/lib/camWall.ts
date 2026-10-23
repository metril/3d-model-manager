/** Which tiles stream live: promoted (clicked) ids first, then list order,
 * capped at `max`. Everything else falls back to periodic snapshots. */
export function selectLive(ids: number[], max: number, promoted: number[] = []): Set<number> {
  const order = [...promoted.filter((p) => ids.includes(p)), ...ids.filter((i) => !promoted.includes(i))];
  return new Set(order.slice(0, Math.max(0, max)));
}

/** Opens the standalone `/camera/$id` window (Q3's card reuses this). */
export function openCameraWindow(printerId: number): Window | null {
  return window.open(`/camera/${printerId}`, `camera-${printerId}`, "popup,width=960,height=600");
}
