import type { LongformJob } from "./LongformWorkspace";
import { serviceUrl } from "./serviceClient";

type EventUpdate = { connected: boolean; items?: LongformJob[] };
const listeners = new Set<(event: EventUpdate) => void>();
let source: EventSource | null = null;
let connected = false;
const notify = (event: EventUpdate) => listeners.forEach(listener => listener(event));

export function subscribeLongformEvents(listener: (event: EventUpdate) => void) {
  listeners.add(listener);
  listener({ connected });
  if (!source && typeof EventSource !== "undefined") {
    source = new EventSource(serviceUrl("/api/longform/events"));
    source.onopen = () => { connected = true; notify({ connected }); };
    source.onerror = () => { connected = false; notify({ connected }); };
    source.onmessage = event => {
      try {
        const update = JSON.parse(event.data) as { type?: string; items?: LongformJob[] };
        if (update.type === "jobs" && Array.isArray(update.items)) notify({ connected: true, items: update.items });
      } catch { /* A malformed event cannot replace the last valid task state. */ }
    };
  }
  return () => { listeners.delete(listener); if (!listeners.size) { source?.close(); source = null; connected = false; } };
}

export function mergeLongformJobs(current: LongformJob[], updates: LongformJob[]) {
  const index = new Map(current.map(job => [job.id, job]));
  for (const update of updates) {
    const previous = index.get(update.id);
    if (!previous || update.version >= previous.version) index.set(update.id, { ...previous, ...update });
  }
  return Array.from(index.values()).sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""));
}
