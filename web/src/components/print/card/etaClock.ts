export function etaClock(minutes: number | null | undefined, now = new Date()): string | null {
  if (minutes == null || !Number.isFinite(minutes) || minutes < 0) return null;
  const end = new Date(now.getTime() + minutes * 60_000);
  const sameDay = end.toDateString() === now.toDateString();
  const hm = end.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? hm : `${end.toLocaleDateString([], { weekday: "short" })} ${hm}`;
}
