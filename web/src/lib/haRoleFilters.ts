import type { HaEntityFilter } from "@/api/haPicker";
import type { HaRole } from "@/api/types";

/** Which HA entities make sense for each link role. */
export const HA_ROLE_FILTERS: Record<HaRole, HaEntityFilter> = {
  power_w: { domain: ["sensor"], deviceClass: ["power"] },
  energy_kwh: { domain: ["sensor"], deviceClass: ["energy"] },
  door: { domain: ["binary_sensor"], deviceClass: ["door", "window", "opening", "garage_door"] },
  smoke: { domain: ["binary_sensor"], deviceClass: ["smoke", "gas", "carbon_monoxide"] },
  temp: { domain: ["sensor"], deviceClass: ["temperature"] },
  humidity: { domain: ["sensor"], deviceClass: ["humidity"] },
};

export const HA_SWITCH_FILTER: HaEntityFilter = { domain: ["switch", "light", "input_boolean"] };
export const HA_POWER_SENSOR_FILTER: HaEntityFilter = HA_ROLE_FILTERS.power_w;
