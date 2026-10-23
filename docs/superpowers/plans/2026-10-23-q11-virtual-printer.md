# Q11 Virtual printer (plan)

Goal: Bambu Studio / Orca see "Virtual A1 Mini" on the LAN, "print" to it, and the 3MF lands in tdmm
(queue, review inbox, archive only, or proxied to a real printer via the dispatcher).

## Components (`backend/app/virtual_printer/`, own compose service, never in printerd)
- `ssdp.py`: UDP 2021 NOTIFY every 30 s + M-SEARCH reply (`urn:bambulab-com:device:3dprinter:1`, DevModel/DevName/DevBind free).
- `mqtt.py`: minimal MQTT 3.1.1 broker over TLS (asyncio, ported from upstream `mqtt_server`; no amqtt/mosquitto dep). User `bblp`, password = access code. Answers `pushall`/`start` with an idle `push_status`, `get_version`, and `project_file` (ack + callback).
- `ftps.py`: implicit-TLS FTPS (asyncio, ported from upstream `ftp_server`; no pyftpdlib dep). `bblp`/access code, PASV/EPSV, STOR into the upload dir, 3MF zip sanity check.
- `certs.py`: self-signed RSA cert (CN = serial) generated on first start into `${data_dir}/virtual-printer/` (named volume `tdmm_data`).
- `intake.py` (sync): rename plain `.3mf` that holds `Metadata/plate_N.gcode` to `.gcode.3mf`; stage -> `slicer_intake.resolve_and_attach_sync`; create `print_queue` row per mode.
- `service.py`: ties it together (FTP upload <-> `project_file` correlation by stripped filename, either order; FTP-only upload with no command after 30 s = archive only), startup port pre-flight.
- `__main__.py`: `python -m app.virtual_printer`; runs the lowest-id enabled row, re-reads DB every 10 s and restarts on change.

## Modes
`archive`: model/file only. `queue`: READY entry, no review. `review`: READY + `review_state=pending` (inbox).
`proxy`: READY entry pinned to `target_printer_id`; the dispatcher plans the AMS mapping against the real printer's trays (Studio's own mapping is ignored: the VP reports no AMS).

## Table `virtual_printers`
id, name, model (SSDP code, e.g. N1), serial (unique), access_code_enc (Fernet), mode (CHECK), target_printer_id (FK printers, SET NULL),
enabled, created_by_user_id (FK users, SET NULL), created_at, updated_at. Revision `a7c3e9d1b5f2` after `c5d8a1e7f3b9`.

## Routes (`/api/virtual-printers`, router-level `require_perm("settings:write")`)
GET list, POST create (server generates serial + 8-digit code), GET/PATCH/DELETE `/{id}` (PATCH may `regenerate_access_code`). Out includes plaintext `access_code`.
`GET /api/features` gains `virtual_printer_enabled` (= `TDMM_VP_ENABLED`).

## Compose / env
Service `virtual-printer`, profile `virtual-printer`, `network_mode: host`, same image, `python -m app.virtual_printer`, mounts `tdmm_data`.
Env: `TDMM_VP_ENABLED`, `TDMM_VP_BIND` (0.0.0.0), `TDMM_VP_PORT_SSDP` (2021), `TDMM_VP_PORT_MQTT` (8883), `TDMM_VP_PORT_FTP` (990),
`TDMM_VP_PASV_MIN/MAX` (50000-50009), `TDMM_VP_PASV_ADDRESS`. Pre-flight: 2021/udp, 8883/tcp, 990/tcp free else exit 2 with a message.
Host networking: DB/Redis are reached via `DATABASE_URL`/`REDIS_URL` in `.env` (must be host-reachable, e.g. localhost + published ports).

## Web
`VirtualPrinterCard` (Settings -> Printer tab, shown when `virtual_printer_enabled`): list/create/edit/delete, shows serial + access code, Studio instructions. Vitest.

## Tests
`test_migrations_q11.py`; `test_virtual_printer.py`: paho (TLS, ephemeral port) connect + pushall -> idle report; ftplib implicit-FTPS upload; `project_file` -> `print_queue` row (queue/review/proxy modes, archive = none); bad access code rejected; port pre-flight fails clearly; routes CRUD; perm sweep.
