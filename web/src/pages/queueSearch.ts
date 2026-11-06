/** `/print/queue` URL contract (T4): `tab` picks queue lanes vs batches;
 * `batch` opens that batch's detail drawer. */
export interface QueueSearch {
  tab?: "queue" | "batches";
  batch?: number;
  /** Deep-link: highlight/scroll to this queue entry. */
  entry?: number;
}

export function parseQueueSearch(search: Record<string, unknown>): QueueSearch {
  const batch = Number(search.batch);
  const entry = Number(search.entry);
  return {
    entry: Number.isInteger(entry) && entry > 0 ? entry : undefined,
    tab: search.tab === "batches" ? "batches" : undefined,
    batch: Number.isInteger(batch) && batch > 0 ? batch : undefined,
  };
}
