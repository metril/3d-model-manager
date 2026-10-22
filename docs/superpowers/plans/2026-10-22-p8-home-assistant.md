# P8 Home Assistant Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or superpowers:executing-plans) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Home Assistant sees every printer as an MQTT-discovered device: sensors, a printing binary_sensor, a camera, and pause/resume/stop buttons. In the other direction, linked HA entities (door, smoke, temp, humidity, power, energy) can hold the dispatcher, optionally pause a running print, and supply kWh for `prints.energy_kwh` → `energy_cost` → `total_cost`, which feeds the P7 charge. If HA is unreachable, the queue runs normally (fail open).

**Architecture:**
- PUSH: `printers/ha_bridge.py` `HaBridge` is a printerd thread, like `CameraSupervisor`. It owns one paho client with an LWT. It reads `printer:{id}:state` from Redis on a 0.5 s tick and publishes on change, at most once per second per printer. It re-publishes the retained discovery on every (re)connect and diffs the printer list against the Redis hash `ha:published` to remove deleted printers. It routes button presses to `printer:{id}:commands`. The pure payloads live in `services/ha_discovery.py`.
- PULL: Celery beat `ha-poll` runs every 15 s and calls `services/home_assistant.poll_once`. That uses the `services/ha_client.py` REST client and writes the Redis keys in `services/ha_state.py`: `ha:alert:{pid}`, `ha:energy:{pid}`, `ha:energy:acc:{pid}` and `ha:status`. `dispatcher.printer_hold_reason` reads `ha_state.hold_reason`; this is the only dispatcher change. printerd snapshots the energy accumulator onto `print_jobs` at start and at terminal. `archive_job` prices the delta.
- Config: Setting row `home_assistant`, with the secrets Fernet-encrypted (`crypto.encrypt_secret`), served by `services/ha_config.py`. `AppConfig.energy_cost_per_kwh` is the energy rate.

**Tech Stack:** paho-mqtt 2.1 (already a dependency, `CallbackAPIVersion.VERSION2`), httpx (sync) + respx, Redis, Celery beat, SQLAlchemy 2 + Alembic, pytest + testcontainers `DockerContainer("eclipse-mosquitto:2.0.20")`; React 19, TanStack Query, shadcn/ui, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-21-bambuddy-parity-design.md` §P8. Also see §Integration rule (`created_by_user_id` on `ha_links`; HA config → `settings:write`), the P4 camera keys (`services/camera_state.py`), the P6 slot (`dispatcher.printer_hold_reason`, already consulted in `_busy_printer_ids`) and the P7 hook (`prints.energy_cost` → `total_cost` → `billing.settle_job`; no billing change needed).

**Branch:** `p8-home-assistant` from `main` after P7 merges. Never commit the user's `compose.yaml` hunk (no task touches compose). Agents never `git stash`/`checkout`. This is the last phase.

## Global Constraints

- Commit: `git -c user.name=metril -c user.email=1517921+metril@users.noreply.github.com commit -m "<type(scope): subject>"`. No trailers, no AI mention.
- **Test discipline:** implementers run ONLY the task's targeted files; the controller runs the full suite. Backend: `cd backend && timeout 600 uv run pytest tests/<file> -q`, then `uv run ruff check . && uv run ruff format --check .` (testcontainers need the sandbox disabled). Web: `cd web && npx vitest run <file>`, then `npm run build` (`tsc --noEmit` is a no-op) and `npx oxlint` (no new warnings vs baseline).
- Migration: one revision `e8b3f1a6c2d4_p8_home_assistant`, `down_revision = "d2a7c4e9f1b3"`.
- Enum `HaRole = energy_kwh|power_w|door|smoke|temp|humidity` (`models/enums.py`, `str_enum(HaRole, "ha_role")`).
- Setting row key `home_assistant`, value `{ha_mqtt_url, ha_mqtt_user, ha_mqtt_password_enc, ha_discovery_prefix, ha_url, ha_token_enc}`. There is no env backing (DB only, set via the card). `ha_discovery_prefix` defaults to `homeassistant`. Secrets are write-only: GET returns `has_mqtt_password`/`has_token`. On PUT, an omitted secret or `"***"` keeps the stored value and `""` clears it.
- `ha_mqtt_url` is `mqtt://host[:1883]` or `mqtts://host[:8883]` (TLS, system CA). Any other scheme, or a URL with userinfo, gives 422. `ha_url` is `http(s)://…` with no trailing slash (normalized).
- Log exception TYPES only on every MQTT/HA failure path (the P2 access-code rule). Never log a password, token, `Authorization` header or a full URL with credentials.
- Topics: base `tdmm`, node `tdmm_<serial.lower()>`, bridge LWT `tdmm/bridge/status` (`online`/`offline`, retained). Discovery: `<prefix>/<component>/<node>/<object_id>/config`, retained, QoS 1. `unique_id = <node>_<object_id>` and device `identifiers = [<node>]` are serial-based, so they are stable across restarts and DB ids.
- Rate: a state payload is published only when it changed AND at least 1.0 s has passed since that printer's last publish. A suppressed change is published on a later tick. Camera: a retained JPEG every 30 s while printing (`gcode_state ∈ {"RUNNING","PREPARE"}`), from `camera_state.get_frame`. While printing, the bridge holds viewer `"ha-bridge"` via `camera_state.add_viewer` every 5 s (`VIEWER_TTL_S = 10`) and removes it when printing stops.
- Redis keys (`services/ha_state.py` is the only builder):
  - `ha:alert:{pid}`: JSON `{reason, entities, hold}` with TTL 60 s, so a dead poller fails open.
  - `ha:alert:fired:{link_id}`: the pause edge trigger, no TTL, deleted on clear.
  - `ha:energy:{pid}`: JSON `{kwh, at}`, no TTL, the previous sample.
  - `ha:energy:acc:{pid}`: a monotonic float, via `INCRBYFLOAT`.
  - `ha:status`: JSON `{ok, error, checked_at}` with TTL 120 s.
  - `ha:bridge:status`: JSON `{connected, error, at}` with TTL 30 s.
  - `ha:published`: hash `pid → "<prefix>|<serial>"`.
- Hold reason text is exactly `Waiting on <entity_id>` (several entities are joined with `, `). The printer hold is computed, not persisted: queue items keep their state, and the API adds `printer_hold_reason`.
- `pause_print` and `hold_queue` default to false. HA button presses act with integration trust (whoever can reach the broker topics). This is documented in the README and is not checked against per-user ACLs.
- Visual tasks (8–9) invoke the `frontend-design` skill first and keep the P3 density tokens. The Held chip uses the existing `held` tone (`text-state-paused border-state-paused`). The HA alert badge is amber with the `Siren` icon. Query keys: `["settings","home-assistant"]`, `["settings","home-assistant","links"]`. Mutations also invalidate `["printers","overview"]` and `["queue"]`.

## Review Focus

1. Discovery layout and stability: topics, `unique_id` and device identifiers are serial-based and byte-identical across restarts, and a deleted printer gets empty retained configs. Task 2 `test_discovery_snapshot`, `test_removal_topics_match`; Task 3 `test_delete_printer_removes_discovery`.
2. Bridge robustness: LWT goes offline on an abrupt drop, everything re-publishes after a broker restart, the per-printer rate is ≤1/s, and `stop()` never hangs. Task 3 `test_lwt_offline_on_drop`, `test_reconnect_after_broker_restart`, `test_rate_gate`, `test_stop_before_connect_returns`.
3. Command routing and pause-on-alert: a button routes to exactly `printer:{id}:commands` `{"command":…}`, unknown payloads are dropped, and an alert pauses only when `pause_print` is set, only on the edge, and only while printing. Task 3 `test_button_routes_to_redis`; Task 5 `test_pause_on_alert_edge_only`.
4. Hold and fail-open: a firing `hold_queue` link holds exactly its printer (or all printers when `printer_id` is NULL), HA unreachable or the poller dead lets dispatch run, and the settings show a warning. Task 4 `test_unreachable_fails_open`; Task 5 `test_dispatcher_skips_held`, `test_alert_ttl_fails_open`.
5. Energy and secrets: the delta survives a counter reset, the cost reaches `total_cost`, and the P7 charge and redelivery do not double count. No password or token appears in any response or log. Task 4 `test_counter_reset_delta`; Task 6 `test_energy_cost_in_total`, `test_energy_redelivery_keeps_value`; Task 1/4/7 `*_secret_never_*`.

---

### Task 1: Schema, models, HA config service, energy rate

**Files:** Create `backend/alembic/versions/e8b3f1a6c2d4_p8_home_assistant.py`, `backend/app/models/home_assistant.py`, `backend/app/services/ha_config.py`, `backend/tests/test_migrations_p8.py`, `backend/tests/test_ha_config.py`. Modify `backend/app/models/{__init__,enums,library}.py` (PrintJob), `backend/app/services/{app_config,secrets_at_rest}.py`, `backend/app/schemas/settings.py` (`AppSettingsIn/Out` += `energy_cost_per_kwh`).

**Interfaces:**
```python
class HaLink(Base):  # "ha_links"; index (printer_id)
    id; printer_id FK printers ON DELETE CASCADE nullable (NULL = all printers); entity_id text NOT NULL;
    role str_enum(HaRole,"ha_role") NOT NULL; alert_condition JSONB nullable;
    hold_queue bool NOT NULL default false; pause_print bool NOT NULL default false;
    created_by_user_id FK users SET NULL index; created_at; updated_at
PrintJob (+): energy_start_kwh Float?; energy_end_kwh Float?      # accumulator snapshots
AppConfig (+): energy_cost_per_kwh: float = 0.0

@dataclass(frozen=True)
class HaConfig: ha_mqtt_url: str|None; ha_mqtt_user: str|None; ha_mqtt_password: str|None  # plaintext, in-process only
                ha_discovery_prefix: str = "homeassistant"; ha_url: str|None; ha_token: str|None
                def __repr__ -> masks both secrets as "***"
                def mqtt_signature(self) -> tuple   # (url, user, password, prefix): bridge reconnect trigger
def parse_mqtt_url(url: str) -> tuple[str, int, bool]          # (host, port, tls); ValueError on bad scheme/userinfo
def get_ha_config_sync(s: Session, settings) -> HaConfig; async def get_ha_config(db, settings) -> HaConfig
async def set_ha_config(db, settings, patch: dict) -> HaConfig  # sentinel merge; encrypts; commits
```
The `alert_condition` shape is `{"op": "eq"|"ne"|"gt"|"gte"|"lt"|"lte"|"in", "value": str|float|list}`, validated in Task 7.
- [ ] Tests:
  - Migration: up adds `ha_links`, the `ha_role` enum and the two `print_jobs` columns. Deleting a printer cascades its links. Down/up round-trips.
  - `test_parse_mqtt_url_matrix`.
  - `test_secrets_encrypted_at_rest`: the raw Setting JSON holds no plaintext.
  - `test_sentinel_keeps_secret` and `test_empty_clears`.
  - `test_repr_secret_never_shown`.
  - `test_energy_rate_defaults_zero` (in `tests/test_app_config_api.py`).
  - `secrets_at_rest` re-encrypts a legacy plaintext `home_assistant` row.
- [ ] Run `timeout 600 uv run pytest tests/test_migrations_p8.py tests/test_ha_config.py tests/test_migration_drift.py tests/test_app_config_api.py -q`. Expect FAIL.
- [ ] Implement. Run → PASS, then `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`.
- [ ] Commit `feat(db): ha_links, job energy snapshots and HA settings`.

### Task 2: MQTT discovery payload builder (pure)

**Files:** Create `backend/app/services/ha_discovery.py`, `backend/tests/test_ha_discovery.py`, `backend/tests/snapshots/ha_discovery_a1mini.json`.

**Interfaces (code body; this is the contract):**
```python
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

def discovery_messages(prefix: str, serial: str, name: str, model: str | None) -> list[tuple[str, dict]]:
    n, t = node_id(serial), topics(serial)
    common = {
        "device": {"identifiers": [n], "name": name, "manufacturer": "Bambu Lab", "model": model or "Bambu"},
        "origin": {"name": "3D Model Manager"},
        "availability": [{"topic": BRIDGE_STATUS}, {"topic": t["availability"]}],
        "availability_mode": "all",
    }
    out: list[tuple[str, dict]] = []

    def add(component: str, oid: str, **extra) -> None:
        out.append((f"{prefix}/{component}/{n}/{oid}/config", {"unique_id": f"{n}_{oid}", **common, **extra}))

    for oid, label, key, unit, dc, sc in SENSORS:
        opt = {k: v for k, v in (("unit_of_measurement", unit), ("device_class", dc), ("state_class", sc)) if v}
        add("sensor", oid, name=label, state_topic=t["state"], value_template=f"{{{{ value_json.{key} }}}}", **opt)
    add("binary_sensor", "printing", name="Printing", device_class="running", state_topic=t["state"],
        value_template="{{ 'ON' if value_json.printing else 'OFF' }}")
    add("camera", "camera", name="Camera", topic=t["camera"])
    for b in BUTTONS:
        add("button", b, name=b.title(), command_topic=t["command"], payload_press=b)
    return out

def removal_topics(prefix: str, serial: str) -> list[str]:
    return [topic for topic, _ in discovery_messages(prefix, serial, "", None)]

def state_payload(public: dict | None, *, last_result: str | None, queue_length: int) -> dict:
    """Keys == SENSORS json keys + 'printing'. layer = 'n/total' or None;
    remaining = mc_remaining_time; state = gcode_state or 'OFFLINE'."""
```
- [ ] Tests:
  - `test_discovery_snapshot`: `discovery_messages("homeassistant", "0309DA123456", "A1 Mini", "A1 Mini")` equals the committed JSON snapshot (14 messages: 9 sensors + binary_sensor + camera + 3 buttons). Generate the snapshot once and review it by hand.
  - `test_unique_ids_stable_and_unique`.
  - `test_removal_topics_match` (the same topic set).
  - `test_custom_prefix`.
  - `test_state_payload_offline_when_none`.
  - `test_state_payload_printing_flag`.
- [ ] Run → FAIL → implement → PASS. Commit `feat(ha): mqtt discovery payload builder`.

### Task 3: `ha_bridge` thread in printerd + mosquitto testcontainer

**Files:** Create `backend/app/printers/ha_bridge.py`, `backend/tests/ha_fixtures.py` (register in `conftest.pytest_plugins`), `backend/tests/test_ha_bridge.py`. Modify `backend/app/printerd.py` (build `self.ha = HaBridge(self.redis, settings, active_ids=lambda: set(self._workers))`, `start()` in `run()` after `camera.start()`, `stop()` in `stop()`).

**Interfaces:**
```python
class RateGate:  # pure
    def __init__(self, min_interval: float = 1.0, clock=time.monotonic)
    def due(self, pid: int, payload: bytes) -> bool   # changed vs last published AND >= min_interval since
    def mark(self, pid: int, payload: bytes) -> None
class HaBridge:
    def __init__(self, r: Redis, settings, *, active_ids: Callable[[], set[int]], tick_s=0.5, config_every_s=10.0,
                 db_every_s=5.0, clock=time.monotonic, client_factory=paho_client)
    def start(self) -> None; def stop(self, timeout: float = 3.0) -> None   # publishes "offline", disconnects, joins
    def tick(self) -> None   # one loop body; tests call it directly
```
Behaviour:
- Config is re-read every 10 s (`get_ha_config_sync`). No `ha_mqtt_url` → idle, disconnected. A changed `mqtt_signature` → disconnect and reconnect. A prefix change first sends empty retained payloads to the old topics.
- The client is created with `will_set(BRIDGE_STATUS, "offline", qos=1, retain=True)`, `reconnect_delay_set(1, 30)`, `connect_async` and `loop_start()`.
- `on_connect` (rc 0): publish `online`, publish every printer's discovery plus per-printer availability, subscribe `tdmm/+/command`, and reset the `RateGate` so current state goes out. It writes `ha:bridge:status`.
- Printers come from the DB every 5 s (all rows, with id, serial, name, model and enabled) together with `last_result` (latest `print_archives.result` per printer, one `DISTINCT ON` query) and `queue_length` (count of `waiting|slicing|ready` per-file items with `printer_id = pid OR printer_id IS NULL`).
- Each tick, for every printer: availability is `online` iff `pid ∈ active_ids()` (retained, published on change). The state payload goes through the `RateGate` (retain=False). The camera viewer heartbeat runs, plus a 30 s camera publish while printing.
- The `ha:published` diff publishes empty retained payloads to `removal_topics` for every pid that is gone, or whose prefix/serial changed, then updates the hash.
- `on_message`: map node → pid via the current printers. If the payload is in `BUTTONS`, `redis.publish(command_channel(pid), json.dumps({"command": payload}))`, else drop with a debug log.
- Failures in the tick are caught and logged as the type only, so the thread never dies.

Fixture: a session-scoped `mosquitto` `DockerContainer("eclipse-mosquitto:2.0.20").with_command("mosquitto -c /mosquitto-no-auth.conf").with_bind_ports(1883, <free port>)`, waiting for the log `running`. The fixed host port lets a restart keep its address. A `mqtt_probe` helper subscribes and collects messages.
- [ ] Tests:
  - Unit, with fake clock and fake client:
    - `test_rate_gate`: 5 changes in 1 s give 2 publishes, and the last value is eventually published.
    - `test_camera_every_30s_while_printing`: the viewer is registered while printing and removed after.
    - `test_no_config_idle`.
    - `test_stop_before_connect_returns` (< 3 s).
  - Integration, against mosquitto:
    - `test_discovery_retained_on_connect`: a late subscriber receives all 14 configs.
    - `test_button_routes_to_redis`: publish `pause` on `tdmm/<node>/command` and the Redis pubsub gets `{"command":"pause"}`; `"rm -rf"` is dropped.
    - `test_delete_printer_removes_discovery`: an empty retained payload on every config topic, and the hash entry is gone.
    - `test_lwt_offline_on_drop`: `loop_stop()` plus a raw socket close lets an observer see `offline`.
    - `test_reconnect_after_broker_restart`: `container.get_wrapped_container().restart()`, then within 40 s `online` and the discovery are re-published.
    - `test_bridge_secret_never_logged`: wrong credentials with caplog show no password.
  - Extend `tests/test_printerd.py`: the daemon starts and stops the bridge.
- [ ] Run `timeout 600 uv run pytest tests/test_ha_bridge.py tests/test_printerd.py -q` → FAIL → implement → PASS.
- [ ] Commit `feat(printerd): home assistant mqtt bridge`.

### Task 4: HA REST client, poll beat, alert/energy Redis keys

**Files:** Create `backend/app/services/{ha_client,ha_state,home_assistant}.py`, `backend/app/tasks/home_assistant.py`, `backend/tests/test_ha_poll.py`. Modify `backend/app/tasks/celery_app.py` (add `"app.tasks.home_assistant"` to `imports`; additive `beat_schedule["ha-poll"] = {"task": "app.tasks.home_assistant.poll_ha", "schedule": 15.0}`).

**Interfaces:**
```python
class HaUnreachable(Exception)
class HaClient:  # httpx.Client(timeout=5), Bearer token; never str(exc) with headers
    def __init__(self, base_url: str, token: str); def ping(self) -> str   # GET /api/ -> "API running."
    def state(self, entity_id: str) -> dict | None   # GET /api/states/<e>; 404 -> None; transport/5xx/401 -> HaUnreachable
# ha_state
def alert_key(pid); energy_key(pid); energy_acc_key(pid); fired_key(link_id); STATUS_KEY = "ha:status"
def read_alert(r, pid) -> dict | None; hold_reason(r, pid) -> str | None   # reason iff alert["hold"]
def energy_acc(r, pid) -> float | None
# home_assistant (pure + orchestration)
def evaluate(cond: dict | None, state: str | None) -> bool  # None cond / unknown|unavailable|None state -> False; numeric ops float() else False
def counter_delta(prev: float | None, cur: float) -> float
def poll_once(s: Session, r: Redis, cfg: HaConfig, client: HaClient | None = None, *, now=None) -> dict
```
`counter_delta` (code body):
```python
def counter_delta(prev: float | None, cur: float) -> float:
    """HA total_increasing semantics: a drop means the meter reset (plug
    re-paired / rolled over), so the whole new reading is consumption since
    the reset. First sample establishes the baseline only."""
    if prev is None:
        return 0.0
    if cur >= prev:
        return cur - prev
    return cur
```
`poll_once`:
- It is a no-op unless `ha_url` and `ha_token` are set and at least one link exists.
- It fetches each distinct `entity_id` once. On `HaUnreachable` it sets `ha:status {ok:false, error:"unreachable"}`, deletes every `ha:alert:*` and returns, which fails open.
- Per printer (a NULL `printer_id` link applies to every printer), the firing links build `{reason: "Waiting on " + ", ".join(entities), entities, hold: any(l.hold_queue)}` as `SET … EX 60`. With nothing firing it DELs the key.
- `energy_kwh` links with a numeric state: `INCRBYFLOAT acc, counter_delta(prev, cur)`, then SET `ha:energy:{pid}`. A non-numeric state keeps `prev`. A NULL-printer energy link is ignored (422 in Task 7).
- An unknown entity (404) goes into `ha:status.entity_errors`. It writes `ha:status {ok:true}`.
- The beat task opens `sync_session`, calls `poll_once`, and uses a Redis lock `ha:poll:lock` (NX, EX 14) so ticks never overlap.
- [ ] Tests (respx):
  - `test_evaluate_matrix`: eq/ne/gt/lt/in, unavailable, non-numeric.
  - `test_counter_reset_delta`: samples 10.0 → 10.4 → 0.1 → 0.3 give acc 0.8.
  - `test_alert_written_with_reason_and_ttl`.
  - `test_null_printer_link_applies_to_all`.
  - `test_clear_deletes_key`.
  - `test_unreachable_fails_open`: an existing alert is removed and `ha:status.ok` is false.
  - `test_404_entity_reported`.
  - `test_ha_token_never_logged`: caplog on a 500 has no token.
  - `test_beat_entry_routes_io`.
- [ ] Run → FAIL → implement → PASS. Commit `feat(ha): poll home assistant entities for alerts and energy`.

### Task 5: Dispatcher hold + pause-on-alert

**Files:** Create `backend/tests/test_ha_hold.py`. Modify `backend/app/services/{dispatcher,home_assistant}.py`.

**Interfaces:**
- `dispatcher.printer_hold_reason(client, pid)` → `ha_state.hold_reason(client, pid)`, and its docstring is updated. Nothing else in the dispatcher changes (`_busy_printer_ids` already consults it).
- `poll_once` also handles the pause edge. For each firing link with `pause_print`, run `SET ha:alert:fired:{link_id} 1 NX`. If the key was newly set, and `read_state_sync(r, pid).gcode_state ∈ PRINTING_STATES`, publish `{"command":"pause"}` to `command_channel(pid)` and log at info "HA alert paused printer %s (%s)". When a link clears, DEL its fired key.
- [ ] Tests:
  - `test_dispatcher_skips_held`: a ready item on held printer A goes to idle printer B; with A only, nothing is dispatched.
  - `test_hold_false_alert_does_not_hold`: an alert with `hold_queue=false` gives a badge but no hold.
  - `test_alert_ttl_fails_open`: the key expires and the item dispatches.
  - `test_pause_on_alert_edge_only`: two polls while firing give one pause; clear then fire again gives a second pause; `pause_print=false` or an IDLE printer gives none.
- [ ] Run (plus `tests/test_dispatcher.py`) → FAIL → implement → PASS. Commit `feat(queue): hold dispatch and pause prints on HA alerts`.

### Task 6: Energy on job start/finish → archive costing

**Files:** Create `backend/tests/test_ha_energy.py`. Modify `backend/app/printerd.py` (`_transition_active_job`), `backend/app/services/{archive,costing}.py`.

**Interfaces:**
- printerd: `energy_start_kwh = ha_state.energy_acc(self.redis, pid)` wherever `started_at` is first set (both the external-job creation and the app-job PRINTING branch). `energy_end_kwh` is set the same way at the terminal branch. Use `contextlib.suppress(Exception)`, so a Redis failure never blocks a transition.
- `costing.job_energy_kwh(start: float|None, end: float|None) -> float|None` returns `round(end - start, 4)` when both are set and `end >= start`, else None.
- `archive_job`: `kwh = job_energy_kwh(...)`. When inserting or `run.energy_kwh is None`, set `run.energy_kwh = kwh`. Pass `energy_cost = None if run.energy_kwh is None else round(run.energy_kwh * cfg.energy_cost_per_kwh, 4)` to `price_run`, so `total_cost` includes it before `settle_job`.
- [ ] Tests:
  - `test_printerd_snapshots_acc_at_start_and_end` (fake adapter, acc 5.0 then 5.6).
  - `test_counter_reset_mid_job`: the poll from Task 4 across a reset during the job gives `energy_kwh == 0.6`.
  - `test_energy_cost_in_total`: at 0.30/kWh the total equals filament + machine + 0.18, and the P7 charge equals the total when billing is on.
  - `test_no_ha_energy_is_none`.
  - `test_energy_redelivery_keeps_value`.
- [ ] Run (plus `tests/test_archive_service.py tests/test_archive_billing.py tests/test_printerd.py`) → FAIL → implement → PASS. Commit `feat(archive): record run energy and price it`.

### Task 7: Settings API: HA config, tests both directions, links CRUD, status surfacing

**Files:** Create `backend/app/api/home_assistant.py`, `backend/app/schemas/home_assistant.py`, `backend/tests/test_ha_settings_api.py`. Modify `backend/app/api/__init__.py`, `backend/app/schemas/{printers,queue}.py`, `backend/app/services/{printer_overview,queue}.py`, `backend/tests/test_auth_sweep.py` (`_PATH_PARAM_VALUES["link_id"]`).

**Interfaces (every route has `require_perm("settings:write")`, GETs included):**
```
GET    /api/settings/home-assistant          -> HaSettingsOut{ha_mqtt_url, ha_mqtt_user, has_mqtt_password, ha_discovery_prefix,
                                                ha_url, has_token, pull_status: ha:status|null, bridge_status: ha:bridge:status|null}
PUT    /api/settings/home-assistant          HaSettingsIn (all optional; sentinel rules; 422 bad mqtt/ha url)
POST   /api/settings/home-assistant/test-mqtt  HaSettingsIn? (unsaved overrides, sentinel -> stored) -> {ok, detail}
                                               paho connect + CONNACK within 5 s in run_in_threadpool; detail = rc reason / exc type
POST   /api/settings/home-assistant/test-rest  -> {ok, detail}   HaClient.ping(); 401 -> "token rejected"
GET    /api/settings/home-assistant/links    -> HaLinkOut[] (with printer_name)
POST   /api/settings/home-assistant/links    HaLinkIn{printer_id?, entity_id ~ ^[a-z_]+\.[a-z0-9_]+$, role, alert_condition?, hold_queue=false, pause_print=false}
PATCH  /api/settings/home-assistant/links/{link_id}; DELETE same (204)
```
- Validation: `alert_condition` uses the Task 1 shape (`gt`..`lte` need a numeric value, `in` needs a list). `role=energy_kwh` requires `printer_id` (422). `hold_queue` or `pause_print` without `alert_condition` gives 422. `created_by_user_id = ctx.user.id`.
- `PrinterOverviewItem` gets `ha_alert: str | None` and `ha_held: bool` from `ha_state.read_alert` (async redis).
- `QueueEntryOut` gets `printer_hold_reason: str | None`. For a targeted entry it is the target printer's hold. For an untargeted entry it is `"All printers held"` only when every enabled printer is held.
- [ ] Tests:
  - `test_get_secret_never_returned` and `test_put_sentinel_keeps`.
  - `test_test_mqtt_ok` and `test_test_mqtt_unreachable` (a closed local port gives `ok:false`, and the detail and caplog hold no password).
  - `test_test_rest_ok_and_401` (respx).
  - Links CRUD + validation 422s.
  - `test_overview_ha_alert`.
  - `test_queue_entry_hold_reason`.
  - Viewer 403 on every route (the sweep).
- [ ] Run (plus `tests/test_perm_sweep.py tests/test_auth_sweep.py tests/test_queue_api.py`) → FAIL → implement → PASS. Commit `feat(api): home assistant settings, links and hold surfacing`.

### Task 8: Web HomeAssistantCard + energy rate

**Files:** Create `web/src/api/homeAssistant.ts`, `web/src/components/settings/HomeAssistantCard.tsx` (+ `.test.tsx`), `web/src/components/settings/HaLinkDialog.tsx`. Modify `web/src/api/types.ts`, `web/src/pages/SettingsPage.tsx` (render when `can("settings:write")`), `web/src/components/settings/PrintCostCard.tsx` (+ test: `energy_cost_per_kwh` field).

**Interfaces:** `useHaSettings()`, `useSaveHaSettings()`, `useTestHaMqtt()`, `useTestHaRest()`, `useHaLinks()`, `useSaveHaLink()`, `useDeleteHaLink()`; types `HaSettings`, `HaLink`, `HaRole`, `HaCondition`.
- The card has two sections:
  - **Publish to HA (MQTT)**: url, user, password (shown as "set" when stored), discovery prefix, Test connection, and a bridge status dot.
  - **Read from HA (REST)**: url, token, Test connection, and an amber warning row when `pull_status.ok === false` ("Home Assistant unreachable, queue holds are ignored") or there are entity errors.
- Link table: entity, printer (or "All"), role, condition (an op select plus a value), Hold queue switch, Pause print switch (with an off-by-default hint "Pauses a running print when the alert fires"), edit and delete. Role presets: door/smoke default to `eq "on"`.
- [ ] Tests: secrets render as "set" and never prefill; both Test buttons call their endpoints and show the result; the warning shows on `ok:false`; adding a door link posts the default condition; the energy role requires a printer; the PrintCostCard energy field saves.
- [ ] Run → FAIL → implement → PASS + build + oxlint. Commit `feat(web): home assistant settings card`.

### Task 9: Web alert badge, Held chip, energy row

**Files:** Create `web/src/components/print/HaAlertBadge.tsx`. Modify `web/src/pages/PrintDashboardPage.tsx` (pass `badge` to `PrinterCard` when `item.ha_alert`), `web/src/pages/PrinterDetailPage.tsx` (the same badge in the header), `web/src/pages/QueuePage.tsx` (+ test), `web/src/components/print/RunDetailSheet.tsx` (+ test), `web/src/api/types.ts`.
- Badge: a `Siren` icon chip, amber, labelled "Held" when `ha_held` and "Alert" otherwise, with the tooltip `ha_alert`. Its aria-label includes the reason.
- QueuePage: when `entry.printer_hold_reason` is set, render a `Held` chip in the `held` tone next to `QueueStateChip`, with the tooltip/aria showing the reason. It is separate from `ReviewChips` (both can show).
- RunDetailSheet: an "Energy used" row (`x.xx kWh`) above the cost rows, shown only when `run.energy_kwh != null`. The existing Energy cost row stays.
- [ ] Tests: the badge renders with the reason; the Held chip with the reason; no chip when null; the energy row only shows with kWh.
- [ ] Run → FAIL → implement → PASS + build + oxlint. Commit `feat(web): HA alert badge, held chip and run energy`.

### Task 10: Docs + live-verify checklist

**Files:** Create `docs/superpowers/plans/2026-10-22-p8-live-verify.md`. Modify `README.md`. The Home Assistant section covers:
- MQTT broker prerequisites: the HA Mosquitto add-on, and that MQTT discovery is on.
- The topic layout and the button trust model.
- REST long-lived token creation.
- Link roles and conditions.
- Fail-open behaviour.
- Energy accounting (the accumulator plus reset handling) and `energy_cost_per_kwh`.

- Live-verify checklist:
  - [ ] save the MQTT settings and see the device "A1 Mini" appear in HA with 9 sensors, Printing, Camera and 3 buttons. Restart printerd and confirm there are no duplicate entities.
  - [ ] press Pause in HA → the A1 Mini pauses. Press Resume → it resumes.
  - [ ] the camera entity updates about every 30 s during a print.
  - [ ] link the door `binary_sensor`, `eq on`, with Hold queue. Open the door → the queue item shows Held "Waiting on binary_sensor.…" and the PrinterCard badge appears. Close it → it dispatches within about 25 s.
  - [ ] stop HA (or use a bad token) → the queue still dispatches and the card shows the warning.
  - [ ] link the smart plug `sensor.*_energy` as energy_kwh and run a print → `prints.energy_kwh` ≈ the plug delta, and `energy_cost` is in `total_cost` (and in the P7 charge when billing is on).
  - [ ] delete a test printer → its HA entities disappear.
  - [ ] grep the printerd/worker logs for the MQTT password and HA token → there are no hits.
- [ ] The controller runs the spec Verification block. Commit `docs(ha): P8 setup and live-verify checklist`.
