"""``stg_cur`` -> human text (table ported from BambuStudio's DeviceManager
via bambuddy's ``bambu_mqtt.STAGE_NAMES``)."""

from __future__ import annotations

STAGE_NAMES: dict[int, str] = {
    0: "Printing",
    1: "Auto bed leveling",
    2: "Heatbed preheating",
    3: "Vibration compensation",
    4: "Changing filament",
    5: "M400 pause",
    6: "Paused (filament ran out)",
    7: "Heating nozzle",
    8: "Calibrating dynamic flow",
    9: "Scanning bed surface",
    10: "Inspecting first layer",
    11: "Identifying build plate type",
    12: "Calibrating Micro Lidar",
    13: "Homing toolhead",
    14: "Cleaning nozzle tip",
    15: "Checking extruder temperature",
    16: "Paused by the user",
    17: "Pause (front cover fall off)",
    18: "Calibrating the micro lidar",
    19: "Calibrating flow ratio",
    20: "Pause (nozzle temperature malfunction)",
    21: "Pause (heatbed temperature malfunction)",
    22: "Filament unloading",
    23: "Pause (step loss)",
    24: "Filament loading",
    25: "Motor noise cancellation",
    26: "Pause (AMS offline)",
    27: "Pause (low speed of the heatbreak fan)",
    28: "Pause (chamber temperature control problem)",
    29: "Cooling chamber",
    30: "Pause (Gcode inserted by user)",
    31: "Motor noise showoff",
    32: "Pause (nozzle clumping)",
    33: "Pause (cutter error)",
    34: "Pause (first layer error)",
    35: "Pause (nozzle clog)",
    36: "Measuring motion precision",
    37: "Enhancing motion precision",
    38: "Measure motion accuracy",
    39: "Nozzle offset calibration",
    40: "High temperature auto bed leveling",
    41: "Auto Check: Quick Release Lever",
    42: "Auto Check: Door and Upper Cover",
    43: "Laser Calibration",
    44: "Auto Check: Platform",
    45: "Confirming BirdsEye Camera location",
    46: "Calibrating BirdsEye Camera",
    47: "Auto bed leveling - phase 1",
    48: "Auto bed leveling - phase 2",
    49: "Heating chamber",
    50: "Cooling heatbed",
    51: "Printing calibration lines",
    52: "Auto Check: Material",
    53: "Live View Camera Calibration",
    54: "Waiting for heatbed temperature",
    55: "Auto Check: Material Position",
    56: "Cutting Module Offset Calibration",
    57: "Measuring Surface",
    58: "Thermal Preconditioning",
    59: "Homing Blade Holder",
    60: "Calibrating Camera Offset",
    61: "Calibrating Blade Holder Position",
    62: "Hotend Pick and Place Test",
    63: "Waiting for Chamber temperature",
    64: "Preparing Hotend",
    65: "Calibrating nozzle clumping detection",
    66: "Purifying the chamber air",
    74: "Preparing",
    77: "Preparing AMS",
}

# -1 / 255 mean "no stage active" (idle).
_IDLE = {-1, 255}


def stage_name(stg_cur) -> str | None:
    """Text for a ``stg_cur`` value; ``None`` when absent/idle/non-numeric."""
    try:
        n = int(stg_cur)
    except (TypeError, ValueError):
        return None
    if n in _IDLE:
        return None
    return STAGE_NAMES.get(n, f"Unknown stage ({n})")
