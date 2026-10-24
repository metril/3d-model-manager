import type { ServerSettingsOut, ServerStatusOut } from "@/api/serverSettings";

export function fakeServer(over: Partial<ServerSettingsOut["config"]> = {}, rest: Partial<ServerSettingsOut> = {}): ServerSettingsOut {
  return {
    config: {
      public_url: null,
      max_upload_bytes: 1073741824,
      max_download_bytes: 2147483648,
      sse_heartbeat_s: 15,
      log_level: "INFO",
      slicer_enabled: true,
      slicer_url: "http://slicer:3001",
      slice_timeout_s: 600,
      vp_bind: "0.0.0.0",
      vp_port_ssdp: 2021,
      vp_port_mqtt: 8883,
      vp_port_ftp: 990,
      vp_pasv_min: 50000,
      vp_pasv_max: 50009,
      vp_pasv_address: null,
      ...over,
    },
    seeded_from_env: {},
    ignored_env: [],
    derived: { cookie_secure: null, request_origin: "http://localhost:3000" },
    ...rest,
  };
}

export function fakeStatus(over: Partial<ServerStatusOut> = {}): ServerStatusOut {
  return {
    slicer: { ok: true, detail: "ok" },
    slicer_healthy: true,
    obico: { ok: true, detail: "ok" },
    virtual_printer: { state: "idle", vp_id: null, bind: null, ports: null, pasv: null, errors: [], ts: null },
    ...over,
  };
}
