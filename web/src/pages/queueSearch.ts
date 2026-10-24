/** `/print/queue` URL contract (T4): `tab` picks queue lanes vs batches;
 * `batch` opens that batch's detail drawer. */
export interface QueueSearch {
  tab?: "queue" | "batches";
  batch?: number;
}

export function parseQueueSearch(search: Record<string, unknown>): QueueSearch {
  const batch = Number(search.batch);
  return {
    tab: search.tab === "batches" ? "batches" : undefined,
    batch: Number.isInteger(batch) && batch > 0 ? batch : undefined,
  };
}
