import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import type { ArchiveOut } from "@/api/types";

export function renderQ(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

export function arch(over: Partial<ArchiveOut> = {}): ArchiveOut {
  return {
    id: 1,
    print_job_id: 10,
    printer_id: 1,
    printer_name: "A1",
    origin: "app",
    subtask_name: "benchy.3mf",
    model_id: null,
    model_slug: null,
    model_name: "Benchy",
    revision_id: null,
    result: "completed",
    duration_s: 3600,
    est_filament: null,
    plate: 1,
    fetch_state: "done",
    fetch_error: null,
    has_thumbnail: false,
    has_file: true,
    has_snapshot: false,
    has_timelapse: false,
    media_note: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-10-20T10:00:00Z",
    created_by_user_id: 1,
    run: null,
    ...over,
  } as ArchiveOut;
}
