export interface Endpoint {
  method: string;
  path: string;
  scope: string;
  desc: string;
  body?: string;
}

export const ENDPOINTS: Endpoint[] = [
  { method: "GET", path: "/printers", scope: "read", desc: "List printers" },
  { method: "GET", path: "/printers/1/status", scope: "read", desc: "Live status" },
  { method: "POST", path: "/printers/1/pause", scope: "control", desc: "Pause the print" },
  { method: "POST", path: "/printers/1/resume", scope: "control", desc: "Resume the print" },
  { method: "POST", path: "/printers/1/stop", scope: "control", desc: "Stop the print" },
  {
    method: "POST",
    path: "/printers/1/control/temperature",
    scope: "control",
    desc: "Set a target (nozzle | bed | chamber). Also: fan, light, speed, home, airduct, clear-hms",
    body: '{"target":"bed","value":60}',
  },
  { method: "GET", path: "/queue", scope: "read", desc: "List the print queue" },
  { method: "POST", path: "/queue", scope: "queue", desc: "Add to the queue", body: '{"model_id":1}' },
  { method: "PATCH", path: "/queue/5", scope: "queue", desc: "Reorder an entry", body: '{"position":1}' },
  { method: "DELETE", path: "/queue/5", scope: "queue", desc: "Cancel / remove an entry" },
  { method: "POST", path: "/files/3/slice", scope: "queue", desc: "Start a slice-only job", body: '{"recipe_id":1}' },
  { method: "GET", path: "/slice-jobs/{id}", scope: "read", desc: "Follow a slice job" },
  { method: "POST", path: "/models", scope: "upload", desc: "Create a model", body: '{"name":"Benchy"}' },
  { method: "PUT", path: "/uploads?model_id=1&revision_id=1&rel_path=benchy.stl", scope: "upload", desc: "Upload a file (raw body)" },
  { method: "POST", path: "/imports", scope: "upload", desc: "Import from a gallery URL", body: '{"url":"https://www.printables.com/model/1"}' },
];

export function curlFor(e: Endpoint, origin: string): string {
  const parts = ["curl"];
  if (e.method !== "GET") parts.push(`-X ${e.method}`);
  parts.push(`-H "X-API-Key: $TDMM_KEY"`);
  if (e.body) parts.push(`-H "Content-Type: application/json" -d '${e.body}'`);
  if (e.method === "PUT") parts.push("--data-binary @benchy.stl");
  parts.push(`"${origin}/api/ext/v2${e.path}"`);
  return parts.join(" ");
}
