import { createContext, useContext } from "react";

import type { JobUpdatedEvent } from "@/api/types";

export type Listener = (event: JobUpdatedEvent) => void;

export interface EventsContextValue {
  /** Register a listener for every `job.updated` event; returns an unsubscribe function. */
  subscribe: (listener: Listener) => () => void;
}

export const EventsContext = createContext<EventsContextValue | null>(null);

export function useEvents(): EventsContextValue {
  const ctx = useContext(EventsContext);
  if (!ctx) {
    throw new Error("useEvents must be used within an EventsProvider");
  }
  return ctx;
}
