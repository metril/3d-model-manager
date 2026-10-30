import { describe, expect, it } from "vitest";

import type { LocationSensor } from "@/api/types";
import { locationClimate, STALE_MS } from "@/lib/locationClimate";

const NOW = Date.parse("2026-10-30T12:00:00Z");
const mk = (o: Partial<LocationSensor>): LocationSensor => ({
  id: 1, location_id: 1, name: "s", entity_id: "sensor.x", kind: "humidity", device_class: "humidity", unit: "%",
  alert_above: null, alert_below: null, notify_on_alert: false, show_on_card: true, sort_order: 0,
  last_state: "30", last_changed_at: new Date(NOW - 60_000).toISOString(), alerting: false, ...o,
});

describe("locationClimate", () => {
  it("is null without humidity/temperature sensors", () => {
    expect(locationClimate([], 60, NOW)).toBeNull();
    expect(locationClimate([mk({ kind: "other", device_class: null })], 60, NOW)).toBeNull();
  });
  it("reads humidity + temperature, fresh and under the threshold", () => {
    const c = locationClimate([mk({}), mk({ id: 2, kind: "temperature", device_class: "temperature", last_state: "21.5" })], 60, NOW)!;
    expect([c.humidity, c.temp, c.stale, c.warn]).toEqual([30, 21.5, false, false]);
  });
  it("warns above the fair threshold or on a firing alert", () => {
    expect(locationClimate([mk({ last_state: "75" })], 60, NOW)!.warn).toBe(true);
    expect(locationClimate([mk({ alerting: true })], 60, NOW)!.warn).toBe(true);
    expect(locationClimate([mk({ last_state: "75" })], undefined, NOW)!.warn).toBe(false);
  });
  it("is stale when old or never updated; ignores unavailable states", () => {
    expect(locationClimate([mk({ last_changed_at: new Date(NOW - STALE_MS - 1).toISOString() })], 60, NOW)!.stale).toBe(true);
    expect(locationClimate([mk({ last_changed_at: null })], 60, NOW)!.stale).toBe(true);
    expect(locationClimate([mk({ last_state: "unavailable" })], 60, NOW)!.humidity).toBeNull();
  });
});
