"""Pure MQTT discovery payload builder for Home Assistant (no I/O)."""

from __future__ import annotations

BASE = "tdmm"
BRIDGE_STATUS = f"{BASE}/bridge/status"
PRINTING_STATES = frozenset({"RUNNING", "PREPARE"})
BUTTONS = ("pause", "resume", "stop")
# object_id, name, json key, unit, device_class, state_class
SENSORS = (
    ("state", "State", "state", None, None, None),
    ("progress", "Progress", "progress", "%", None, "measurement"),
    ("remaining", "Remaining", "remaining", "min", "duration", "measurement"),
    ("layer", "Layer", "layer", None, None, None),
    ("nozzle_temp", "Nozzle temperature", "nozzle_temp", "°C", "temperature", "measurement"),
    ("bed_temp", "Bed temperature", "bed_temp", "°C", "temperature", "measurement"),
    ("current_job", "Current job", "current_job", None, None, None),
    ("last_result", "Last result", "last_result", None, None, None),
    ("queue_length", "Queue length", "queue_length", None, None, "measurement"),
)


def node_id(serial: str) -> str:
    return f"{BASE}_{serial.strip().lower()}"


def topics(serial: str) -> dict[str, str]:
    n = node_id(serial)
    return {k: f"{BASE}/{n}/{k}" for k in ("state", "availability", "camera", "command")}


def discovery_messages(
    prefix: str, serial: str, name: str, model: str | None
) -> list[tuple[str, dict]]:
    n, t = node_id(serial), topics(serial)
    common = {
        "device": {
            "identifiers": [n],
            "name": name,
            "manufacturer": "Bambu Lab",
            "model": model or "Bambu",
        },
        "origin": {"name": "3D Model Manager"},
        "availability": [{"topic": BRIDGE_STATUS}, {"topic": t["availability"]}],
        "availability_mode": "all",
    }
    out: list[tuple[str, dict]] = []

    def add(component: str, oid: str, **extra) -> None:
        out.append(
            (
                f"{prefix}/{component}/{n}/{oid}/config",
                {"unique_id": f"{n}_{oid}", **common, **extra},
            )
        )

    for oid, label, key, unit, dc, sc in SENSORS:
        opt = {
            k: v
            for k, v in (("unit_of_measurement", unit), ("device_class", dc), ("state_class", sc))
            if v
        }
        add(
            "sensor",
            oid,
            name=label,
            state_topic=t["state"],
            value_template=f"{{{{ value_json.{key} }}}}",
            **opt,
        )
    add(
        "binary_sensor",
        "printing",
        name="Printing",
        device_class="running",
        state_topic=t["state"],
        value_template="{{ 'ON' if value_json.printing else 'OFF' }}",
    )
    add("camera", "camera", name="Camera", topic=t["camera"])
    for b in BUTTONS:
        add("button", b, name=b.title(), command_topic=t["command"], payload_press=b)
    return out


def removal_topics(prefix: str, serial: str) -> list[str]:
    return [topic for topic, _ in discovery_messages(prefix, serial, "", None)]


def state_payload(public: dict | None, *, last_result: str | None, queue_length: int) -> dict:
    """Keys == SENSORS json keys + 'printing'. layer = 'n/total' or None;
    remaining = mc_remaining_time; state = gcode_state or 'OFFLINE'."""
    if public is None:
        return {
            "state": "OFFLINE",
            "progress": None,
            "remaining": None,
            "layer": None,
            "nozzle_temp": None,
            "bed_temp": None,
            "current_job": None,
            "last_result": last_result,
            "queue_length": queue_length,
            "printing": False,
        }

    gcode_state = public.get("gcode_state") or "OFFLINE"
    layer_num = public.get("layer_num")
    total_layer_num = public.get("total_layer_num")
    layer = (
        f"{layer_num}/{total_layer_num}"
        if layer_num is not None and total_layer_num is not None
        else None
    )

    return {
        "state": gcode_state,
        "progress": public.get("mc_percent"),
        "remaining": public.get("mc_remaining_time"),
        "layer": layer,
        "nozzle_temp": public.get("nozzle_temper"),
        "bed_temp": public.get("bed_temper"),
        "current_job": public.get("subtask_name"),
        "last_result": last_result,
        "queue_length": queue_length,
        "printing": gcode_state in PRINTING_STATES,
    }
