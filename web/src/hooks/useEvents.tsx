/**
 * Singleton SSE connection for the app (Task 8 decision): one `EventSource`
 * created here, in the authenticated layout (`AppShell`), exposing a
 * `subscribe` API for pages that care about specific jobs (the upload
 * queue), and centrally invalidating model queries whenever a job
 * completes so Files/Revisions tabs pick up newly-stored files without a
 * manual refresh.
 */
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/api/client";
import type { AppEvent } from "@/api/types";
import i18n from "@/i18n";
import { EventsContext, type EventsContextValue, type Listener } from "@/hooks/eventsContext";

// Minimum time between session probes triggered by `onerror` (below) --
// a real network blip can fire `onerror` repeatedly as the browser retries
// the connection, and there's no reason to hammer `/auth/me` for each one.
const ERROR_PROBE_THROTTLE_MS = 5000;

export function EventsProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const listenersRef = useRef(new Set<Listener>());
  const lastErrorProbeRef = useRef(0);

  useEffect(() => {
    const source = new EventSource("/api/events");
    let opened = false;

    // A re-open (not the first) means events may have been lost while the
    // connection was down -- refetch everything the stream keeps fresh.
    source.onopen = () => {
      if (!opened) {
        opened = true;
        return;
      }
      for (const key of ["queue", "jobs", "models", "printers", "print-jobs"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    };

    source.onmessage = (event: MessageEvent<string>) => {
      let parsed: AppEvent;
      try {
        parsed = JSON.parse(event.data) as AppEvent;
      } catch {
        return;
      }

      // Coarse print-job transitions (M4 Task 8): the high-frequency
      // telemetry (percent/layer/temps) is NOT delivered here -- it's
      // polled from `usePrinterStatus`. This event only means "go refetch
      // the job list / printer status", so it's a plain invalidation with
      // no listener fan-out.
      if (parsed.type === "print_job.updated") {
        void queryClient.invalidateQueries({ queryKey: ["print-jobs"] });
        // `["printers"]` prefix-matches `["printers", "overview"]` under
        // react-query's default partial matching -- the overview (which
        // embeds each printer's active job + recent runs, P3 Task 3) is
        // already covered by this one invalidation.
        void queryClient.invalidateQueries({ queryKey: ["printers"] }); // status polled separately
        void queryClient.invalidateQueries({ queryKey: ["queue"] });
        void queryClient.invalidateQueries({ queryKey: ["archives"] });
        void queryClient.invalidateQueries({ queryKey: ["spools"] });
        return;
      }

      // A slice (queue entry or standalone job) changed state: refresh the
      // queue and the followed job; a finished one also adds a file.
      if (parsed.type === "slice.updated") {
        void queryClient.invalidateQueries({ queryKey: ["queue"] });
        if (parsed.slice_job_id) {
          void queryClient.invalidateQueries({ queryKey: ["slice-job", parsed.slice_job_id] });
        }
        if (parsed.state === "done") {
          void queryClient.invalidateQueries({ queryKey: ["models"] });
          void queryClient.invalidateQueries({ queryKey: ["revisions"] });
        }
        return;
      }

      if (parsed.type === "orcaslicer.file_returned") {
        const slug = parsed.model_slug;
        void queryClient.invalidateQueries({ queryKey: ["models", "detail", slug] });
        if (parsed.sliced) void queryClient.invalidateQueries({ queryKey: ["models", "list"] });
        toast(i18n.t("orcaslicer.returned", { name: parsed.name }), {
          action: {
            label: i18n.t("orcaslicer.viewModel"),
            onClick: () => {
              void import("@/router").then((m) => m.router.navigate({ to: "/models/$slug", params: { slug } }));
            },
          },
        });
        return;
      }

      if (parsed.type === "orcaslicer.session_started") {
        void queryClient.invalidateQueries({ queryKey: ["orcaslicer", "session"] });
        return;
      }

      if (parsed.type !== "job.updated") return;

      // The Jobs page (Task 9) lists every job type, live -- refresh it on
      // every transition (queued/running/done/failed/dead), not just the
      // terminal ones the models/revisions invalidation below cares about.
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });

      if (parsed.state === "done" || parsed.state === "failed") {
        void queryClient.invalidateQueries({ queryKey: ["models"] });
        void queryClient.invalidateQueries({ queryKey: ["revisions"] });
      }

      // The scanner reuses this same `job.updated` shape for its progress
      // (Task 5 brief: "no new SSE event type"), with `job_type:
      // "scan_library"` on every state transition (queued/running/done/
      // failed/skipped) -- refresh the Scan report live for all of them,
      // not just the terminal ones above.
      if (parsed.job_type === "scan_library") {
        void queryClient.invalidateQueries({ queryKey: ["scan"] });
      }

      // Gallery imports reuse the job.updated shape (M5; no new SSE type) with
      // job_type: "import_from_url" -- refresh the import poll + the gallery so
      // a finished import's model appears without a manual reload.
      if (parsed.job_type === "import_from_url") {
        void queryClient.invalidateQueries({ queryKey: ["imports"] });
        void queryClient.invalidateQueries({ queryKey: ["models"] });
      }

      for (const listener of listenersRef.current) listener(parsed);
    };

    source.onerror = () => {
      // The browser retries a dropped `EventSource` connection forever and
      // silently -- `onerror` carries no status code, so an expired session
      // (the backend 401ing the reconnect) looks identical to a transient
      // network blip. Probing an authenticated endpoint directly is the
      // only way to tell: `api.get`'s 401 handler (client.ts) redirects to
      // /login itself, which is what actually ends the infinite reconnect
      // loop for a real expired session.
      const now = Date.now();
      if (now - lastErrorProbeRef.current < ERROR_PROBE_THROTTLE_MS) return;
      lastErrorProbeRef.current = now;
      void api.get("/auth/me").catch(() => {});
    };

    return () => source.close();
  }, [queryClient]);

  const value = useMemo<EventsContextValue>(
    () => ({
      subscribe: (listener) => {
        listenersRef.current.add(listener);
        return () => {
          listenersRef.current.delete(listener);
        };
      },
    }),
    [],
  );

  return <EventsContext.Provider value={value}>{children}</EventsContext.Provider>;
}
