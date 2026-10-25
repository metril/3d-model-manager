"""Event catalog: ids, labels, default templates and preview samples (Q6)."""

from __future__ import annotations

from typing import Any

# id -> (label, description, default title, default body, sample payload)
_EVENTS: dict[str, tuple[str, str, str, str, dict[str, Any]]] = {
    "job_started": (
        "Print started",
        "A print job begins.",
        "Print started: {{ subtask_name|default('print') }}",
        "{{ printer_name }} started {{ subtask_name|default('a print') }}.",
        {"subtask_name": "benchy.3mf", "job_id": 1},
    ),
    "job_finished": (
        "Print finished",
        "A print job completed successfully.",
        "Print finished: {{ subtask_name|default('print') }}",
        "{{ printer_name }} finished {{ subtask_name|default('a print') }}.",
        {"subtask_name": "benchy.3mf", "job_id": 1},
    ),
    "job_failed": (
        "Print failed",
        "A print job failed or was cancelled.",
        "Print failed: {{ subtask_name|default('print') }}",
        "{{ printer_name }} failed {{ subtask_name|default('a print') }}"
        "{% if error %} (error {{ error }}){% endif %}.",
        {"subtask_name": "benchy.3mf", "job_id": 1, "error": "0300400C"},
    ),
    "job_paused": (
        "Print paused",
        "A print job was paused (by you, an error or the filament runout sensor).",
        "Print paused: {{ subtask_name|default('print') }}",
        "{{ printer_name }} paused {{ subtask_name|default('a print') }}.",
        {"subtask_name": "benchy.3mf", "job_id": 1},
    ),
    "hms_error": (
        "Printer fault (HMS)",
        "The printer reported a new HMS fault code.",
        "{{ printer_name }}: {{ code }}",
        "{{ message|default('New HMS fault') }} ({{ code }})"
        "{% if severity %} - {{ severity }}{% endif %}",
        {
            "code": "0300_0100_0001_0001",
            "message": "Heatbed temperature abnormal",
            "severity": "serious",
        },
    ),
    "dev_mode_off": (
        "Developer mode turned off",
        "The printer's Developer Mode switched off; remote control stops working.",
        "{{ printer_name }}: developer mode is off",
        "Remote control and sending jobs will fail until Developer Mode is "
        "re-enabled on {{ printer_name }}.",
        {},
    ),
    "filament_low": (
        "Filament low",
        "A spool dropped below its low-stock threshold.",
        "Filament low: {{ spool_name|default('spool') }}",
        "{{ spool_name|default('A spool') }} has {{ remaining_g|default('little') }} g left.",
        {"spool_name": "PLA Black", "remaining_g": 80},
    ),
    "queue_item_added": (
        "Queue item added",
        "Someone added a print to the queue.",
        "Queued: {{ name|default('print') }}",
        "{{ user|default('Someone') }} added {{ name|default('a print') }} to the queue.",
        {"name": "benchy.3mf", "user": "alice"},
    ),
    "review_requested": (
        "Review requested",
        "A print needs approval before it can start.",
        "Review needed: {{ name|default('print') }}",
        "{{ user|default('Someone') }} requested approval for {{ name|default('a print') }}.",
        {"name": "benchy.3mf", "user": "alice"},
    ),
    "backup_failed": (
        "Backup failed",
        "A scheduled backup did not complete.",
        "Backup failed",
        "The backup failed{% if error %}: {{ error }}{% endif %}.",
        {"error": "disk full"},
    ),
    "drying_started": (
        "Drying started",
        "An AMS unit started drying filament.",
        "Drying started on {{ printer_name }}",
        "AMS {{ ams_id }} on {{ printer_name }} started drying"
        "{% if filament %} ({{ filament }}{% if temp %} @ {{ temp }} C{% endif %}){% endif %}.",
        {"ams_id": 0, "filament": "PETG", "temp": 65},
    ),
    "drying_finished": (
        "Drying finished",
        "An AMS unit finished drying.",
        "Drying finished on {{ printer_name }}",
        "AMS {{ ams_id }} on {{ printer_name }} finished drying.",
        {"ams_id": 0},
    ),
    "drying_failed": (
        "Drying failed",
        "A scheduled drying run could not start or never began.",
        "Drying failed on {{ printer_name }}",
        "Scheduled drying on AMS {{ ams_id }} of {{ printer_name }} failed"
        "{% if error %} ({{ error }}){% endif %}.",
        {"ams_id": 0, "error": "did_not_start", "filament": "PETG"},
    ),
    "failure_suspected": (
        "Failure suspected",
        "Print-failure detection flagged a possible failure.",
        "Possible failure on {{ printer_name }}",
        "Failure detection scored {{ score|default('high') }} on {{ printer_name }}.",
        {"score": 0.92},
    ),
    "print_confirm_request": (
        "Print outcome check",
        "Asks whether a finished print came out well (tap a link to answer).",
        "How did {{ subtask_name|default('the print') }} turn out?",
        "{{ printer_name }} finished {{ subtask_name|default('a print') }}. "
        "Good: {{ good_url }} Bad: {{ bad_url }}",
        {
            "subtask_name": "benchy.3mf",
            "good_url": "https://tdmm.example/api/archives/confirm/TOKEN/good?tap=1",
            "bad_url": "https://tdmm.example/api/archives/confirm/TOKEN/bad?tap=1",
        },
    ),
    "location_sensor_alert": (
        "Storage sensor alert",
        "A storage-location sensor (e.g. humidity) crossed its alert threshold.",
        "{{ location|default('Storage') }}: {{ sensor|default('sensor') }} alert",
        "{{ sensor|default('Sensor') }} at {{ location|default('a storage location') }} reads "
        "{{ state }}{{ unit }} ({{ direction|default('out of range') }} "
        "{{ threshold }}{{ unit }}).",
        {
            "location": "Dry cabinet",
            "sensor": "Humidity",
            "entity_id": "sensor.dry_cabinet_humidity",
            "state": "62",
            "unit": "%",
            "direction": "above",
            "threshold": 50,
        },
    ),
}

EVENT_IDS = tuple(_EVENTS)
TEMPLATE_VARS = ["event", "printer_name", "printer_id", "now", "(plus the event's fields)"]


def default_template(event: str) -> tuple[str, str]:
    e = _EVENTS.get(event)
    if e is None:
        return "{{ event }}", "{{ event }} on {{ printer_name|default('printer') }}"
    return e[2], e[3]


def sample_payload(event: str) -> dict[str, Any]:
    e = _EVENTS.get(event)
    return dict(e[4]) if e else {}


def catalog() -> list[dict[str, str]]:
    return [{"id": k, "label": v[0], "description": v[1]} for k, v in _EVENTS.items()]
