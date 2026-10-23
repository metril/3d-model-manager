"""Recorded NORMALIZED status snapshots -- the shape app.printers.bambu._snapshot
produces and printerd stores. The bambulabs_api lib owns the raw MQTT merge,
so these pin only the logic WE still own: snapshot -> PrinterPublicState and
gcode_state -> PrintJobState."""

SNAPSHOT_IDLE = {
    "gcode_state": "IDLE",
    "mc_percent": 0,
    "layer_num": 0,
    "total_layer_num": 0,
    "mc_remaining_time": 0,
    "print_error": 0,
    "nozzle_temper": 24.0,
    "bed_temper": 23.0,
    "subtask_name": "",
    "wifi_signal": "-42dBm",
}
SNAPSHOT_PRINTING = {
    "gcode_state": "RUNNING",
    "mc_percent": 55,
    "layer_num": 66,
    "total_layer_num": 120,
    "mc_remaining_time": 21,
    "print_error": 0,
    "nozzle_temper": 220.0,
    "bed_temper": 60.0,
    "subtask_name": "widget",
    "wifi_signal": "-45dBm",
}
SNAPSHOT_FINISH = {
    "gcode_state": "FINISH",
    "mc_percent": 100,
    "mc_remaining_time": 0,
    "print_error": 0,
    "subtask_name": "widget",
}
SNAPSHOT_ERROR = {"gcode_state": "FAILED", "print_error": 83935248, "subtask_name": "widget"}

# Recorded-shape A1 Mini ``mqtt_dump()`` (raw pushall: top-level {"print":..., "info":...}),
# idle, 0.4 mm nozzle, no AMS, no chamber/aux fan. Fan speeds arrive as 0-15 strings.
A1_MINI_DUMP = {
    "print": {
        "gcode_state": "IDLE",
        "stg_cur": 255,
        "print_error": 0,
        "wifi_signal": "-52dBm",
        "nozzle_diameter": "0.4",
        "nozzle_target_temper": 0,
        "bed_target_temper": 60,
        "nozzle_type": "hardened_steel",
        "nozzle_temper": 24.5,
        "bed_temper": 59.8,
        "cooling_fan_speed": "8",
        "big_fan1_speed": "0",
        "big_fan2_speed": "0",
        "spd_lvl": 2,
        "sdcard": True,
        "hms": [{"attr": 83886336, "code": 65545}],
        "lights_report": [{"node": "chamber_light", "mode": "on"}],
        "ipcam": {"ipcam_record": "enable", "resolution": "720p"},
        "ams": {
            "ams": [
                {
                    "id": "0",
                    "humidity": "4",
                    "humidity_raw": "31",
                    "temp": "24.6",
                    "sn": "AMS123",
                    "tray": [],
                }
            ]
        },
        "vt_tray": {"id": "254", "tray_type": "PLA", "tray_color": "FF0000FF"},
    },
    "info": {
        "module": [
            {"name": "ota", "sw_ver": "01.07.00.00"},
            {"name": "ams_f1/0", "sw_ver": "00.00.06.49"},
            {"name": "rv1126", "sw_ver": "00.00.14.74"},
        ]
    },
}
