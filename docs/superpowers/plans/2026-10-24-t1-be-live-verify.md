# T1-be live verification (A1 Mini)

The A1 Mini (`verified=True`) is the only live printer. AMS-lite cannot dry, so the live pass proves the
negative paths and the humidity reading; drying payloads are fixture-only.

1. `GET /api/printers/{id}/status`: `capabilities.ams_drying=false`, `drying_screen_only=false`,
   `ams_drying_while_printing=false`; `ams_units[]` carries `dry_status` (0/null), empty `dry_sf_reason`.
2. Web: the drying button / unit chip is hidden for the A1 Mini (capability gate).
3. `POST /api/printers/{id}/drying/start {"ams_id":0,"temp":55,"duration_hours":4}` -> `422 {"code":"unsupported"}`;
   `.../drying/stop {"ams_id":0}` -> `422 unsupported`; `POST .../drying/schedules` -> `422`.
4. Humidity: each `ams_units[].humidity` is the relative-% from `humidity_raw` (compare with the printer screen);
   a unit reporting only the 1-5 index shows `humidity=null` and `humidity_index` set (never converted).
5. Settings: `GET /api/settings/drying` returns the seeded presets; PUT round-trips; out-of-range values 422.
6. Beat: with `ambient_drying_enabled` on, the `drying.tick` log shows no schedule created for the A1 Mini
   (unsupported) and no error; `docker compose logs beat worker | grep drying`.

Drying-capable models (H2D/H2S/P2S/X1/X1C/X1E/H2D Pro) remain `verified=False` ("untested" hint). Their
`info` bit layout (`dry_status` bits 4-7, `dry_sub_status` bits 22-25) is covered by a SYNTHESIZED fixture
(`tests/cassettes/bambu_snapshots.py AMS_DRYING_DUMP`), not a capture. On first real hardware: start a short
cycle, confirm `dry_status` 1->2->3->0, `dry_time_min` counting down, `dry_target_*` populated then cleared,
a `dry_sf_reason` code while a print runs, the plug `auto_off_after_drying` once on cycle end, and the
`drying_started/finished` notifications. Open question: whether `duration` on the wire is hours on every
model (upstream sends hours; confirm on an H2D).
