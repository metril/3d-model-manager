export function formatHistoryT(hours: number) {
  return (ts: number) =>
    new Date(ts).toLocaleString(undefined, hours > 24 ? { month: "short", day: "numeric", hour: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
}
