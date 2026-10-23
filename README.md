# 3D Model Manager

Self-hosted web app for managing a personal library of 3D-printable models.

- **Library**: STL, 3MF (incl. Bambu Production Extension), OBJ, STEP, IGES, plus sliced `.gcode.3mf`/`.gcode` artifacts
- **Storage**: pluggable backends — local directory, SMB share (userspace client, no privileged container), S3 — human-readable tree, every file content-hashed (blake3); configured/connection-tested from Settings, with a local→X migration helper
- **Scanner**: `scan_library` reconciles the DB against the backend on demand (or on a schedule) — relinks moved folders by hash without re-hashing untouched files, adopts out-of-band folders dropped straight onto the share as draft models, flags genuinely missing files for review; never deletes anything
- **Revisions**: model-level, full snapshot per revision (fast-copied via reflink / SMB COPYCHUNK / S3 CopyObject), hash-diff between revisions
- **Viewing**: server-side normalization to meshopt-compressed GLB; in-browser rotate/zoom/pan viewer; server-rendered thumbnails (f3d) + embedded 3MF plate thumbnails
- **Printing**: send sliced files to a Bambu Lab A1 mini over LAN (Developer Mode: FTPS upload + MQTT start/status)
- **Import**: by URL from Printables, Thingiverse, and (feature-flagged) MakerWorld, with provenance/license capture
- **Stack**: FastAPI + Celery/Redis + PostgreSQL backend; React 19 + Vite + Tailwind 4 + shadcn/Radix frontend; Docker Compose deployment
- **Processing pipeline**: per-blob background jobs extract mesh/CAD metadata (dims, triangle count, volume) and sliced-file metadata (print time, filament, per-plate previews), convert STL/OBJ/3MF/STEP/IGES to GLB, meshopt-compress it for the browser (`gltfpack`), and render server-side thumbnails (`f3d`) -- covering plain meshes, CAD (via OCCT), and Bambu Production-Extension/sliced `.gcode.3mf` 3MFs
- **Viewer**: in-browser React Three Fiber viewer (orbit/pan/zoom) rendering the compressed GLB, with an automatic decimated LOD for very high-poly meshes
- **Gallery**: cover thumbnails, per-format badges, sliced-file filtering (`has_sliced`) and print-time display, driven by the same processing pipeline

Design spec: [docs/superpowers/specs/2026-07-04-3d-model-manager-design.md](docs/superpowers/specs/2026-07-04-3d-model-manager-design.md)
Research notes: [docs/research/](docs/research/)

## Status

[![CI](https://github.com/metril/3d-model-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/metril/3d-model-manager/actions/workflows/ci.yml)
[![Release](https://github.com/metril/3d-model-manager/actions/workflows/release.yml/badge.svg)](https://github.com/metril/3d-model-manager/actions/workflows/release.yml)

Pre-alpha — under active development. See the spec for the M0–M6 milestone plan.

## Quickstart (Docker Compose)

Requires Docker with the Compose plugin, **v2.24 or newer** (Feb 2024) — the
production overlay uses Compose's `!reset` tag to suppress the from-source
build. Check with `docker compose version`.

```sh
cp .env.example .env
docker compose -f compose.yaml -f compose.prod.yaml pull
docker compose -f compose.yaml -f compose.prod.yaml up -d
```

Adding `-f compose.prod.yaml` is what makes this the *pull* path: the overlay
points every app service at the published
`ghcr.io/metril/3d-model-manager` image instead of building locally. That
matters more than it sounds — a from-source build compiles `gltfpack` from C++
and installs OCCT and `f3d`, which takes **15-25 minutes**. Pulling takes as
long as your connection does.

**Pin a release** rather than tracking `latest`, so an upgrade is something you
choose rather than something that happens to you on the next `up`:

```sh
IMAGE_TAG=v0.1.0 docker compose -f compose.yaml -f compose.prod.yaml up -d
```

`IMAGE_TAG` is read from your shell or from the root `.env` (Compose
auto-loads that file for interpolation), so putting `IMAGE_TAG=v0.1.0` in
`.env` alongside everything else is the tidier option and applies to every
subsequent command without repeating it. Unset, it defaults to `latest`. See
"Releases & versioning" below for what each published tag means.

All five app services — `api`, `worker-io`, `worker-cpu`, `beat`, `printerd` —
run this **one** image; they differ only by the `ROLE` env var and their
`command`. There is nothing to pull per-service.

Confirm what's actually running:

```sh
curl -s localhost:8080/api/health
# {"status":"ok","version":"0.1.0"}
```

The published image is **`linux/amd64` only** for now — an arm64 build is
blocked on an upstream dependency, and [docs/arm64-status.md](docs/arm64-status.md)
records exactly which one and what would unblock it.

### Building from source instead

For development, or on a platform with no published image, omit
`-f compose.prod.yaml` — that alone selects `compose.yaml`'s `build:` blocks:

```sh
cp .env.example .env
docker compose up -d --build
```

Expect the 15-25 minute first build described above. Subsequent builds reuse
Docker's layer cache and are much faster.

### What comes up

Either path brings up the same stack.

This starts every service, always — there are no compose profiles to opt
into: `api` (port `8080`), `worker-io` (uploads/store), `worker-cpu` (the
metadata/GLB-conversion/thumbnail pipeline, one process per core,
memory-recycled -- see "Worker split" below), `beat` (ticks the scan/
collection-sync/watched-folder dispatcher), `printerd` (the Bambu LAN MQTT
supervisor), `db` (Postgres 16), and `redis`. `beat` and `printerd` idle
harmlessly when their corresponding features are off in Settings — see
"Scheduled scan" and "Printer integration" below. The api container runs
Alembic migrations on startup, then serves both the JSON API (`/api/...`)
and the built frontend SPA at <http://localhost:8080>.

Models and files are written to `./library` on the host (bind-mounted); job
spool state and other app data live in the `tdmm_data` named volume.

**First-run admin password**: if `ADMIN_PASSWORD` is left unset in
`.env`, the api container generates a random password on first boot and
prints it exactly once, at `WARNING` level, to its logs:

```sh
docker compose logs api | grep -i password
```

Copy it down immediately — it is not recoverable afterwards (short of
resetting the `db` volume). To pin a known password instead (e.g. for
scripting), set `ADMIN_PASSWORD` in `.env` before the first `up`.
`ADMIN_PASSWORD` only ever seeds the account on that first boot; change the
password afterwards from **Settings → General**, not by editing `.env`.

To tear the stack down (keeping data): `docker compose down`. To also drop
the database/volumes: `docker compose down --volumes`.

**Worker split**: the processing pipeline's CPU-heavy steps (metadata
extraction, GLB conversion via OCCT/trimesh/lib3mf, `gltfpack` optimization,
`f3d` thumbnail rendering) run on `worker-cpu`, routed there by queue name;
everything else (`store_to_backend`, the upload path) runs on `worker-io`.
`worker-cpu` runs one process per core with no fixed concurrency, and
recycles each child process after 16 tasks or 1.5 GiB RSS, whichever comes
first, to contain memory growth from OCCT (STEP/IGES) and f3d. Both workers
expose a `celery inspect ping` healthcheck.

**Running as a non-root user (PUID/PGID)**: by default the api/worker
containers run as root, so files written under the bind-mounted `./library`
end up root-owned on the host. Set `PUID`/`PGID` in `.env` (e.g. to your
`id -u`/`id -g`) to have the entrypoint drop privileges to that uid/gid
instead (linuxserver.io convention) — it also `chown`s the existing
`./library`/`tdmm_data` contents to match on every start, so this is safe to
turn on after the fact. Verify the drop works without standing up the whole
stack:

```sh
docker build -f docker/Dockerfile -t tdmm:local .
docker run --rm -e PUID=1000 -e PGID=1000 -e ROLE=api tdmm:local id -u
# -> 1000
```

## Users, groups and single sign-on

Accounts have a role (`admin`, `member`, `viewer`) and can be placed in groups that grant extra permissions and per-printer access (Admin pages). Local password login always works; OIDC providers are added under Admin, with the client secret stored encrypted and never returned.

Authentik example:

1. Create an OAuth2/OpenID provider, client type confidential, with redirect URI `https://<host>/api/auth/oidc/authentik/callback` and scopes `openid profile email groups`.
2. In TDMM add a provider with slug `authentik`, discovery URL `https://auth.example/application/o/<slug>/.well-known/openid-configuration`, the client ID and secret, and `groups_claim = groups`.
3. Map IdP group names to TDMM groups (`group_mapping`), and optionally set `admin_group` to promote its members to admin on each login. The last active admin is never demoted by a sync.
4. Accounts are matched by issuer and subject. Enable `link_by_email` to attach an existing account by e-mail (only when the IdP reports `email_verified: true`; require it in Authentik), and `auto_create_users` to create `member` accounts on first login.

Keycloak: use `groups_claim = realm_access.roles`. Set `TDMM_PUBLIC_URL` when running behind a reverse proxy so the callback origin is correct.

## Upgrading

**Routine upgrade** (published image). Point `IMAGE_TAG` at the new release,
pull, and bring the stack back up:

```sh
IMAGE_TAG=v0.2.0 docker compose -f compose.yaml -f compose.prod.yaml pull
IMAGE_TAG=v0.2.0 docker compose -f compose.yaml -f compose.prod.yaml up -d
curl -s localhost:8080/api/health
# {"status":"ok","version":"0.2.0"}
```

That `/api/health` check is the whole verification step — it reports the
version baked into the image that is actually running, not the one you meant
to deploy. If it still shows the old version, the containers didn't get
recreated.

**There is no separate migration step.** The `api` container runs Alembic
migrations on startup, so bringing it up on a new image is what applies them.
But **migrations are one-way** — there is no downgrade path, and rolling
`IMAGE_TAG` back to the previous release does *not* roll the schema back. An
older image against a newer schema is not a supported configuration.

So: **take a database backup before upgrading.** See "Backup & Restore"
below for the `pg_dump` command and, just as importantly, the
`printer.key` that has to travel with it.

From a source checkout, the equivalent is:

```sh
git pull
docker compose up -d --build
```

If you're upgrading a deployment from before the Settings-UI move (Round
10): the five old env vars (`PRINTER_ENABLED`, `SCAN_INTERVAL`,
`COLLECTION_SYNC_INTERVAL`, `WATCH_INTERVAL`, `WATCH_STABLE`) and
`COMPOSE_PROFILES`/`--profile` are all obsolete. On the first boot after
upgrading, whichever of the five env vars are still set in `.env` seed the
new database-backed settings exactly once (a startup warning in the api
logs names any it saw); every boot after that ignores them completely, and
all further changes happen live from the Settings UI. Once you've
confirmed the values under **Settings → General → Automation & scheduling**
and **Settings → Printer** look right, delete those lines from `.env` —
they no longer do anything.

## Releases & versioning

Releases are automated by
[release-please](https://github.com/googleapis/release-please), driven by
[Conventional Commits](https://www.conventionalcommits.org/). The commit
message prefix on `main` decides the next version:

- `feat:` → **minor** bump
- `fix:` → **patch** bump
- a breaking change (`feat!:`, or a `BREAKING CHANGE:` footer) → also a
  **minor** bump while the project is pre-1.0, not a jump to `1.0.0`
  (`bump-minor-pre-major` in `release-please-config.json`) — a "Pre-alpha"
  project reaching 1.0 by accident helps nobody

Other prefixes (`chore:`, `docs:`, `refactor:`, `test:`) don't trigger a
release on their own.

release-please keeps a **release PR permanently open** against `main`, and
rewrites it on every push: its diff is the pending `CHANGELOG.md` entry and
the version bumps. Nothing is published while it sits there. **Merging that
PR is the act of cutting a release** — it creates the `vX.Y.Z` tag, commits
the changelog, publishes the image ladder below, and opens a GitHub Release
with the extension attached. `CHANGELOG.md` is generated by that process; it
is never hand-edited.

### Published image tags

All on `ghcr.io/metril/3d-model-manager`:

| Tag | Moves? | Use it for |
|---|---|---|
| `v0.1.0` | Never | **Production.** An exact release, byte-for-byte reproducible. |
| `0.1` | Yes — newest patch in that minor series | Automatic patch updates, no feature changes. |
| `0` | Yes — newest release in that major series | Pre-1.0 this carries **no compatibility promise**; it exists for ladder symmetry. Don't deploy it. |
| `latest` | Yes — newest stable release | Casual/first-time deployments. |
| `edge` | Yes — **every** push to `main` | Testing unreleased work. **Not supported**: it can contain schema changes whose migrations are not in any release. |
| `sha-<short>` | Never | Pinning one specific commit, e.g. bisecting a regression. |

Every image reports its own build, so you never have to infer it from the tag
you think you pulled — `GET /api/health` returns it, and it's also
`info.version` in `/api/openapi.json` (and so in the `/docs` header). The
value is one of:

- a release semver, e.g. `0.1.0`
- `edge-<short sha>` for a build from a `main` push
- `dev` for an unstamped local `docker build` with no `APP_VERSION` build-arg

### Browser extension

The extension is versioned **in lockstep with the app** — same number, always
— and each release publishes it as a sideload zip
(`tdmm-extension-<version>.zip`, with a `.sha256` sidecar) attached to the
GitHub Release. Install and verification steps are in
[extension/README.md](extension/README.md).

## Storage backends & the scanner

The active storage backend (local directory, SMB, or S3 — exactly one at a
time) is chosen and configured from **Settings → Storage** in the running
app, not via `.env`: each backend's connection details (SMB host/share/
credentials, S3 bucket/keys/endpoint) are validated, connection-tested
before they take effect, and stored in the database. Switching backends
offers a **local→X migration** job that copies existing content over before
the switch flips live.

**SMB**: address the NAS by IP or a real, resolvable DNS name — never a bare
mDNS/`.local`/NetBIOS name, which the container's resolver can't see. For a
LAN-only DNS name, add an `extra_hosts:` entry for it to the `api`/
`worker-io`/`worker-cpu` services in `compose.yaml`:

```yaml
    extra_hosts:
      - "nas.lan:192.168.1.50"
```

**Rescan/reconcile**: trigger a scan from Settings → Storage (or `POST
/api/scan`) to reconcile the database against whatever is actually on the
backend — useful after reorganizing files directly on a share/NAS outside
the app. A scan relinks moved folders by hash (without re-hashing files
that didn't move), adopts folders dropped straight onto the share as new
draft models for review, and flags files present in the database but
missing on disk for a human to resolve. It never deletes library content or
database rows. **Manual `Scan now` runs immediately.** To also run scans on
a schedule, set a positive interval under **Settings → General →
Automation & scheduling** — `beat` (always running) picks the change up
within ~15 s, and (arm-and-skip) the first automatic run lands one full
interval after you enable it, not immediately.

Followed remote collections/favourites can sync on the same kind of
schedule instead of only via the Settings **"Sync now"** button (also
immediate) — set the collection-sync interval in the same **Settings →
General → Automation & scheduling** panel; the same ~15 s apply latency and
one-interval arm delay apply.

## Printer integration (Bambu LAN, feature-flagged)

**Off by default.** Enable it live, no restart, with the toggle at
**Settings → Printer** — `printerd` (always running) picks the change up
on its next reconcile tick. With the toggle off (the default), the app is
fully usable: the printers API 503s and the Printer nav hides.

**Printer prerequisites**: an A1/A1 mini on firmware **≥ 01.05.00.00**. On
the printer's screen, enable **LAN-only Mode**, power-cycle it, then enable
**Developer Mode** in the same menu; the **access code** shows on the
LAN-only screen (toggle LAN-only off and back on if it displays zeros). A
healthy **microSD card is mandatory** — without one, uploads fail with an
"Insert SD card" error. Pin the firmware version that's known to work for
you, and re-verify connectivity after every firmware update using the
setup wizard's **Test connection** (an MQTT connect + `pushall` probe).

**Tradeoff**: Developer Mode requires LAN-only Mode, which disconnects the
printer from **Bambu Cloud and the Bambu Handy app**. Deliver firmware
updates via microSD instead, or temporarily re-enable cloud connectivity
when you need one.

**ToS / responsibility**: LAN Developer Mode is a user-enabled,
**Bambu-unsupported** escape hatch — enabling it is your decision and your
responsibility. This app never uses the Bambu Connect signed cloud path.

**Security**: the printer's access code is stored **Fernet-encrypted**
(`printers.access_code_enc`) using a key at `${DATA_DIR}/secrets/
printer.key` (mode `0600`, auto-generated and shared across api/worker/
printerd via the `tdmm_data` volume) or supplied explicitly via
`PRINTER_KEY`. It is decrypted only inside the worker/printerd
processes, and is never returned by the API or written to logs. **TLS
verification is off in v1**: the printer's MQTT/FTPS/camera ports present a
self-signed certificate from Bambu's private CA that no system trust store
accepts (and there's no hostname to check against), so — like every other
LAN client — this app disables verification rather than trusting nothing. A
**TOFU (trust-on-first-use) certificate pin remains deferred** — pinning
needs the real A1 mini's self-signed certificate in hand, so it pairs with
the deferred live-hardware acceptance below.

**Sending**: only sliced **`.gcode.3mf`** files (Bambu Studio's "Export
plate sliced file") can be sent to a printer — a bare `.gcode` is rejected.
A print won't start unless the printer is idle (state ∈ `IDLE`/`FINISH`/
`FAILED`); a printer that's busy, offline, or of unknown state is rejected
before anything is uploaded.

Register a printer from **Settings → Printer** (host, serial, access code)
— the setup wizard runs **Test connection** against it before saving.

### Print archive & run history

Every finished print job (completed, failed, or stopped) is archived: the
sliced 3MF and its plate thumbnail are stored as content-addressed blobs and
a run is added to **Printer → history** with filament grams, duration and
cost. The 3MF comes from the local library file when the job was sent from
the app, otherwise it is fetched from the printer's SD card over FTPS.

**External prints**: jobs started from Bambu Studio or the printer's
touchscreen are detected from the MQTT state, recorded as external jobs and
archived the same way when they finish (best effort, if the 3MF can still be
found on the printer).

**Cost** = filament (`grams / 1000 x filament cost per kg`) + machine
(`hours x machine cost per hour`) (+ energy when available), using the rates
in Settings. Completed runs charge the full estimated grams; failed or
stopped runs are charged **partially**, by printed fraction (`layer /
total_layers`, or progress % when layers are unknown).

**Good / Reject**: grade a run in the history sheet; rejected runs stay in
the history but are flagged as wasted.

**Storage**: archived files live under `{data_dir}/archives/` (inside the
`tdmm_data` volume), so they are covered by the normal data backup; include
that directory in any manual backup.

**Reprint** from an archive rebuilds a print plan from the job's own sent
file (falling back to any library file sharing the archived 3MF's blob hash),
proposing the same AMS/spool mapping the original run used.

### Spools & filament tracking

**Spool inventory** (`/spools`) tracks physical filament spools: material,
color, label weight, remaining weight (`label_weight_g - weight_used_g`),
cost per kg, and a low-stock threshold. Usage is recorded per print job
against whichever spool was mapped to each filament, so weight and cost stay
accurate without manual bookkeeping.

**AMS slot assignment**: each AMS tray (and the external/vt_tray spool
holder) can hold a spool assignment. A spool with an RFID tag that's been
read by the AMS **auto-assigns** to its slot; third-party/non-RFID spools are
assigned manually from the printer card. Slot ids follow Bambu's own
encoding (`ams_unit * 4 + tray`, with `254`/unit `255` reserved for the
external spool) so the mapping sent to the printer lines up with what the
touchscreen shows.

**Usage history**: the spool page and per-printer stats show filament usage
over time, broken down by material and by individual spool, sourced from
each archived run's actual (3MF-estimated or AMS-delta-inferred) grams.

**Spoolman integration is tabled** — this app does its own spool tracking
rather than talking to a Spoolman instance; there is no plan to add that
integration currently.

### Printing from the app

**Files → Print** opens a single modal that combines plate/AMS/calibration
options with filament mapping: for each filament the plate's 3MF calls for,
pick (or let RFID auto-assign) the spool/AMS tray that will supply it,
including the external spool holder for single-color prints. Sending
confirms the plan, uploads the 3MF, and starts the print — the printer must
be idle first (see **Sending**, above).

Once the job finishes, the run is archived as usual (see **Print archive &
run history**) and each mapped spool's `weight_used_g` is advanced by that
filament's grams, so inventory and run cost reflect the spool(s) actually
used rather than a flat filament-cost assumption.

### Slicing, profiles & recipes

**Off by default, like printer integration.** A *recipe* pairs a printer
profile, a process profile and (optionally) filament profiles into one
named preset, so **Files → Print**'s Profile step can offer "slice and
send" for an unsliced source file (3MF/STL/STEP/OBJ) instead of requiring
an already-sliced `.gcode.3mf`. Enable it with
`docker compose --profile slicer up -d` (or `COMPOSE_PROFILES=slicer` in
`.env`), which builds and starts the `slicer` sidecar (`./slicer`, a
FastAPI wrapper around a pinned Bambu Studio CLI, `compose.slicer.yaml`).
`TDMM_SLICER_URL` (`.env.example`) points the API at it; unset,
no slicer integration and recipe actions stay disabled (`GET /api/slicer/
health` reports `configured: false`).

**Profiles** (`/inventory/profiles`) are the raw printer/process/filament
JSON a slicer bundle exports, each tagged with a `kind` and (for printer
profiles) the `printer_model` string recipes filter by. Import them either
from a Bambu Studio **profile bundle** (`.zip`/folder containing a
`BBL.json` manifest — the same tree Bambu Studio writes under its own
config directory, e.g. `~/.config/BambuStudio/system/`\* on Linux or
`%APPDATA%\BambuStudio\system\` on Windows) via **Import**, or by pulling
**Sync Bambu system profiles** (Profiles page / Slicer integration card),
which pulls the sidecar's bundled Bambu presets — only your configured
printers' models by default, or all of them. Synced rows are tagged
`synced`: a re-sync refreshes them but never overwrites imported or
user-edited profiles. A profile edited after import takes
effect on the very next slice — nothing is cached inside the sidecar
itself.

**Recipes** (the same page's Recipes tab) combine a printer + process
profile, optional filament profiles, a `bed_type` (must match one of the
BBL `curr_bed_type` strings the pinned CLI accepts, not an arbitrary
label), pre-print `options`, and a **fanout**: `single` (any one eligible
printer), `spread` (one copy per eligible idle printer) or `first_n`
(the first *n* eligible printers, by name order). Recipes are listed for
Profile-step selection filtered to printers whose profile `printer_model`
matches the one already chosen in the plan, or all of them when none has
been picked yet.

\* Bambu Studio's exact config path varies by install/version — check
**Help → Show Configuration Folder** inside Bambu Studio if the above
doesn't match.

### Queue & dispatch

Each `/api/queue` entry can now carry its own `file_id`, `plate`,
`printer_id`, `recipe_id`, `ams_mapping`, pre-print `options` and `copies`
(1–50), tracked through a state machine: `waiting` (has a recipe, not yet
sliced) → `slicing` → `ready` (has a sliced file — where an as-sliced
"Add to queue" starts directly) → `dispatched` (a print job sent) →
`done`/`failed`/`held`. **Files → Print**'s "Slice and send" inserts at
the front of the queue (`waiting`); "Add to queue" appends at the end.

A beat task (`dispatch_queue`, every 10s, independent of the printer
status poll's own cadence) drives the whole pipeline: slicing a handful of
`waiting` items per tick (bounded so a long backlog doesn't flood the CPU
worker queue), then handing each `ready` item to the first eligible idle
printer — "eligible" meaning permitted (`can_print`), compatible
(printer model match when the recipe names one; nozzle diameter within
0.05mm when both the recipe and the printer's last-reported MQTT
`nozzle_diameter` are known — the check is skipped, not failed, when
either side is unknown), and `IDLE`/`FINISH`. A `spread`/`first_n` recipe
splits its `copies` one-per-printer across every eligible idle printer (up
to *n* for `first_n`); when a printer **FINISH**es, the next queued item
targeting it is picked up on the next tick automatically. A dispatch
failure (sidecar down, send rejected, etc.) retries up to 3 attempts total
before the entry goes `failed`; a sidecar-down slicing failure does **not**
consume an attempt. Revoking a user's `inventory:write`/`printers:control`
permission after they enqueued an item holds it (`held`) rather than
silently dispatching on their behalf.

`docker compose up` **without** `--profile slicer` still starts every other
service — the slicer sidecar is additive, same posture as the printer
integration.

### Job review & budgets

OFF by default: billing is a runtime toggle, not an env var. An admin or
`billing:manage` holder flips it at **Finance** (`/system/finance`) —
`PUT /finance/config {"billing_enabled": true}` — no restart needed. While
off, every send/queue dialog shows a plain **Send now** and no ledger rows
are written; a dialog that was already open when billing turns off still
settles normally (its print isn't retroactively blocked).

**Review gate** (independent of billing): a printer's ACL can carry
`requires_review`. A member sending/queueing to a `requires_review`
printer who doesn't themselves hold `queue:review` lands `Pending`
instead of dispatching. A reviewer with `queue:review` over that printer
sees it in **Review inbox** and can **Approve** (dispatches/prints
normally) or **Reject** (`Rejected`, and any reservation already posted
is released). Billing being on also routes new items into review even
on printers that don't require it, so a budget can be checked before
dispatch.

**Cost centers**: a budget bucket owned by exactly one user (a personal
allowance, lazily created) or exactly one group, never both. Each has an
optional `budget_total` (lifetime cap) and `budget_monthly` (resets each
calendar month) — either left `NULL` means unlimited on that axis.
Reviewers/managers see every center; everyone else sees only their own
and any their group owns. A queued item against a center reserves its
estimated cost on dispatch; if the reservation would push the center over
either budget, the item holds (`over_budget`) instead of dispatching. A
manager can post a **deposit** (`billing:manage`) to raise the balance,
which unholds and dispatches anything the new headroom covers. On
completion the reservation is released and a **charge** for the print's
actual `total_cost` is posted (a run cancelled partway posts a charge for
only the elapsed/partial cost); the ledger for a given item is always
`reservation` → `release` → `charge`.

**Nightly recompute**: a beat task (`recompute_balances`, 00:05 UTC)
recomputes every cost center's `balance` from its ledger rows, logs a
warning for any center whose cached balance had drifted from the
recomputed one, and retries every `over_budget`-held item (covers a
monthly-cap item that's now unheld because the new month reset its
spend window).

### Live camera & timelapse

A printer tile and run view can show a **live camera feed** (MJPEG, ~5 fps,
multiplexed so multiple tabs share one upstream connection to the printer)
and, once a run finishes, its **snapshot** and **timelapse** video attached
to the archive row. **A1-only**: this talks to the Bambu A1-class local
camera protocol (port 6000) directly — it does not work with other Bambu
printer families. Timelapse video on the printer is stored as AVI and
**transcoded to MP4 with ffmpeg** in the worker image before it's attached;
without ffmpeg in the image the timelapse is fetched but not converted.

### Manual/Live Acceptance (deferred — user present, real A1 mini, NOT automated)

The physical "the A1 mini actually starts printing" drill needs a real
printer on the LAN and a person watching the first print start, so it is
**not** part of the automated test suite (see `backend/tests_e2e/
test_m4_printer.py` for the hardware-free flag-off/wizard/preflight
coverage that *is* automated). When hardware is available, run this once
against a real A1 mini:

1. On the printer: firmware ≥ 01.05, enable **LAN-only Mode** → power-cycle
   → enable **Developer Mode**; note the access code.
2. Flip the toggle on at **Settings → Printer**, then add the printer
   there (host/serial/access code); **Test connection** should report
   `ok:true` with a real `gcode_state`.
3. Export a plate as `.gcode.3mf` from Bambu Studio, upload it to a model,
   open **Files → Print**, pick the plate/AMS/calibration options, and
   **Send** — **the A1 mini starts the print** (the load-bearing
   acceptance for this milestone).
4. On `/printers/{id}`: confirm live **%/layer/remaining/temps** update; that
   **Pause/Resume/Stop** work; that **sending while RUNNING is blocked**;
   and that pulling the **microSD card** surfaces an actionable SD-missing
   error.

Record the outcome in the milestone ledger; file any firmware-drift
findings against the pinned-firmware note above.

## Home Assistant integration

**Off by default** — nothing publishes or polls until you save settings at
**Settings → Home Assistant**. There is no env-var backing; the row lives
in the database (secrets Fernet-encrypted) and is write-only (the API
returns `has_mqtt_password`/`has_token`, never the secrets themselves).

**Push (MQTT discovery)**: install the **Mosquitto broker add-on** in HA
(or point at any broker reachable from `printerd`) and make sure **MQTT
discovery** is enabled in HA's MQTT integration — this app never calls the
HA REST API to create entities, it only publishes retained discovery
payloads. `printerd` runs one `HaBridge` thread that publishes a device per
printer under node `tdmm_<serial>`: 9 sensors (state, progress, remaining,
layer, nozzle/bed temp, current job, last result, queue length), a
`Printing` binary_sensor, a `Camera` entity (retained JPEG, refreshed every
~30 s while printing), and three buttons (**Pause/Resume/Stop**). Unique
ids and device identifiers are derived from the printer's **serial**, so
they're stable across a `printerd` restart or a DB id change — no
duplicate entities. A deleted printer's discovery is retracted (empty
retained payloads) and its entities disappear from HA.

**Button trust model**: pressing Pause/Resume/Stop in HA publishes straight
to `tdmm/tdmm_<serial>/command`, and the bridge forwards whatever lands on
that topic to the printer. **Buttons are trusted** — anyone who can publish
to the broker's `tdmm/#` topics can pause or stop a print, with no
per-user ACL check on this app's side. Secure the broker (auth, and TLS if
it's reachable beyond your LAN) accordingly; don't expose it unauthenticated.

**Pull (REST)**: create a **long-lived access token** in HA (your HA user
profile → Security → Long-lived access tokens) and paste it into the
**Read from HA** section along with the HA base URL. A `ha-poll` beat task
polls every 15 s and evaluates the entities named in your **links**.

**Links**: each link names an `entity_id`, a **role**
(`door`/`smoke`/`temp`/`humidity`/`power`/`energy_kwh`), an optional
numeric/string `alert_condition` (`eq`/`ne`/`gt`/`gte`/`lt`/`lte`/`in`), and
toggles **Hold queue** and **Pause print**. A link can target one printer
or (`printer_id` left empty) **all printers**; an `energy_kwh` link must
name a printer. When a link's condition fires, the matching printer(s) show
`Held "Waiting on <entity_id>"` and the dispatcher skips them; `Hold queue`
is what makes it hold (a firing alert without it just shows a badge).
`Pause print` additionally pauses an already-running print, once per
rising edge (not repeatedly while the condition stays true).

**Fail-open**: if HA is unreachable (bad token, HA down, network drop) or
the poller itself dies, the alert keys expire (60 s TTL) and **the queue
dispatches normally** — a broken HA integration never blocks printing.
The settings card shows a warning when the last poll failed.

**Energy accounting**: for an `energy_kwh` link (typically a smart plug's
`sensor.*_energy` total), the poller accumulates deltas into a Redis
counter using HA's `total_increasing` semantics — if a sample drops below
the previous one, the meter reset (plug re-paired, counter rolled over),
so the new reading is treated as consumption since the reset rather than
a negative delta. `printerd` snapshots the accumulator at a job's start and
finish; the difference becomes `prints.energy_kwh`, priced by
`energy_cost_per_kwh` (**Settings → Print cost**) into `energy_cost`, which
is folded into `total_cost` and, when billing is on, the P7 cost-center
charge.

## Bambu Studio integration

**No plugin hook exists.** Bambu Studio has no plugin SDK, and its
`bambustudio://` deep links are domain-allowlisted to `makerworld.com`
inside the binary — a self-hosted app has no way to register itself as a
target for Studio's own "send"/"open in" actions. The two loops below,
built on features Studio already exposes, are the supported path instead.

**1. Auto-upload every slice (metadata).** In Studio: **Process → Others →
Post-processing scripts**, add `python3 /path/to/bambu_postprocess.py`.
Studio runs it after every slice and appends the sliced file's path as the
last argument; the script itself (`scripts/bambu_postprocess.py` in this
repo, also downloadable from **Settings → Accounts → Slicer
integration**) reads two environment variables from wherever Studio
itself runs (set them in your shell profile, or wrap the command in a
small launcher — Studio doesn't let you pass any extra arguments of your
own):

- `INTAKE_URL` — e.g. `http://<this host>:8080/api/slicer/intake`
- `INTAKE_TOKEN` — an API token minted from **Settings → Accounts →
  Slicer integration**

Every sliced plate is uploaded and matched to an existing model by name,
or a new model is created if none matches. **This path only produces a
plain `.gcode`** — useful for print history/metadata, but not something
the app can send to a printer.

**2. Printable file (watched folder).** For a file you can actually print
from the app, use Studio's **File → Export → Export plate sliced file**
(`.gcode.3mf`) into a folder this instance watches. It's imported the same
way (matched/created by name), and because it's a real sliced plate, the
resulting file gets the **Send-to-printer** button (Files tab and Print
Queue). This is opt-in and OFF by default; enable it with:

- The poll interval and mtime-stability window, both under **Settings →
  General → Automation & scheduling** — a positive poll interval turns
  watching on (`0` means off); the stability window (default `10` s) is
  how long a file's mtime must be quiet before it's imported, guarding
  against importing an export that's still being written. `beat` (always
  running) picks up an edit within ~15 s.
- `WATCH_HOST_DIR` — the host directory to point Studio's
  export at, bind-mounted to `/watch` inside `worker-io` by
  `compose.yaml` (env-configured; there's no UI for this one since it's
  container topology, not a runtime feature)

A dropped file with an unrecognized extension is moved into `.failed/`
inside the watched folder; successfully imported files move into
`.imported/`.

Both loops authenticate with the **same bearer-token plane the browser
extension uses** — mint or revoke tokens from **Settings → Accounts →
Slicer integration** (or **Browser extension**; either card manages the
same token list, so a token from one works for the other).

## Gallery importers

**What it does.** Paste a **Thingiverse** or **Printables** model URL on
`/add`; the app detects the site, downloads every file into a new
library model with full attribution (source link, author, license, tags),
and runs it through the normal ingest pipeline (lands as
`rev-001_imported`). Imports are **atomic** — a failed or paid/Club model
creates no model.

**Thingiverse token setup.** Thingiverse requires a personal **App Token**
to download files: go to `thingiverse.com/apps/create`, register a
**"Desktop app"**, copy the **App Token**, and paste it in **Settings →
Gallery site tokens**. The token is stored masked and never shown again
(blank the field to keep the stored one). Printables needs **no token**
(free models only; **Club/paid models are rejected with a clear
message** — they require a Printables login).

**MakerWorld — not yet.** MakerWorld import is **deferred to a later
milestone** (it needs a Bambu-account login). Pasting a MakerWorld URL
shows a friendly "not available yet" message rather than failing.

**ToS / personal use.** These importers are for **personal use, one model
per action**; by importing you confirm the model's license permits it. The
`/add` page header carries this note. Respect each site's Terms of
Service and the model's license (imported CC attribution is preserved on
the model page).

**Reliability.** The Thingiverse API is historically flaky and the
Printables GraphQL schema is unofficial; both are isolated behind one
module each with recorded-fixture contract tests, and a failed import
never corrupts library state. A `live_importer`-marked smoke test per site
(deferred/manual, excluded from the default gate — see below) hits the
real API on demand.

## Backup & Restore

Three things hold state; back up all three together and they must stay in
sync on restore.

**1. The Postgres database.** Holds models/revisions/files, jobs, settings
(the storage-backend config with its **encrypted** SMB/S3 secret and the
**encrypted** Thingiverse token), and printers (`access_code_enc`). No host
port is published for `db`, so dump through the container:

```sh
docker compose exec db pg_dump -U tdmm tdmm > backup.sql
```

**2. `${DATA_DIR}/secrets/printer.key`** (or the matching
`PRINTER_KEY` value). **This is the critical one.** As of M6 this single
Fernet key decrypts the printer access codes **and** the SMB/S3 storage
secret **and** the Thingiverse token. **Lose it — or restore a database
against a *different* key — and every encrypted secret becomes permanently
undecryptable** (`InvalidToken`); everything else restores fine, but you'll
be re-entering every credential by hand. The DB dump and this key are a
matched pair: back them up together, restore them together. (Same key
described under [Printer integration → Security](#printer-integration-bambu-lan-feature-flagged).)

**3. The `library/` contents.** For the **local** backend this is the
`./library` bind-mount on the host — copy it like any directory. For the
**SMB** or **S3** backends the library lives on your own share/bucket, which
this app does not manage — back it up with your NAS/S3 tooling. Derivatives
are never stored here (see below).

**Do _not_ bother backing up:**
- `${DATA_DIR}/derivatives/**` — thumbnails and GLBs, all **regenerable**
  by re-running the pipeline against the library originals (the migrate task
  documents "derivatives always stay local"). Skipping them keeps backups
  small.
- `${DATA_DIR}/spool/**` — transient in-flight upload bytes, meaningless
  after the fact.

**Restore runbook:**

1. Bring up a fresh `db` service and load the dump:
   ```sh
   docker compose up -d db
   docker compose exec -T db psql -U tdmm tdmm < backup.sql
   ```
2. Put `printer.key` back into the `tdmm_data` volume at
   `${DATA_DIR}/secrets/printer.key` (mode `0600`), **or** set the same
   `PRINTER_KEY` in `.env` — **before** starting api/worker/printerd, so
   the encrypted secrets decrypt and the eager startup re-encryption pass
   (idempotent, safe on already-encrypted data) doesn't run against the wrong
   key.
3. Restore `library/` (local backend) or confirm the SMB share / S3 bucket is
   reachable.
4. `docker compose up -d` — Alembic migrations run automatically on api boot.

The env vars that must match the backed-up stack: `DATABASE_URL`,
`DATA_DIR`, and the `db` service's `POSTGRES_USER` / `POSTGRES_PASSWORD`
/ `POSTGRES_DB`.

> **Warning:** `docker compose down --volumes` destroys **both** the `pgdata`
> and `tdmm_data` volumes — that is your database, your derivatives, your
> **secrets** (`printer.key`), and the spool, gone together and
> irrecoverably. Like the first-run admin password, none of it comes back
> without a backup. Use plain `docker compose down` (no `--volumes`) to stop
> the stack while keeping state.

**Acceptance (deferred — one-time manual restore drill, not automated):**
like the printer's [live acceptance](#manuallive-acceptance-deferred--user-present-real-a1-mini-not-automated),
a full restore is verified by hand, not in the test suite: `pg_dump` a seeded
stack, `docker compose down --volumes`, then restore the DB + `printer.key` +
`library/` into a fresh stack and confirm the gallery renders, a printer's
access code still decrypts (Test connection succeeds), and a stored SMB/S3
secret still works — proving the key travelled with the DB.

## Development

### Backend

```sh
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8080
```

Requires a Postgres and Redis reachable at the `DATABASE_URL` /
`REDIS_URL` defaults (`localhost:5432` / `localhost:6379`) — the
easiest way is `docker compose up -d db redis`. Run the Celery worker
alongside the api for upload/ingest and pipeline processing:

```sh
uv run celery -A app.tasks.celery_app worker -Q io,cpu --concurrency=2 -l info
```

**M2 pipeline prerequisites**: run `scripts/fetch-gltfpack.sh` once (fetches
the `gltfpack` CLI, used by the `optimize_glb` step, into `backend/.tools/`
via npm — outside the uv-managed Python dependencies). `f3d` thumbnail
rendering needs no extra install for local dev: it falls back to Mesa's EGL
offscreen renderer automatically when OSMesa isn't available. `libosmesa6`
is only installed inside the Docker image, for that GPU-less container's
OSMesa path.

Tests (real Postgres + Redis via `testcontainers`, no mocks):

```sh
uv run pytest        # unit/integration suite (e2e + live_importer excluded via the -m in addopts)
uv run ruff check .
uv run ruff format --check .
```

The full Docker-based end-to-end flow (build image, run compose stack, drive
the M1 upload/revision/diff/download/restart flow, the M2 metadata/GLB/
thumbnail pipeline flow, the M3 scan drill — move a folder on the
bind-mounted share, rescan, relink by hash, download-verify; drop an
untracked folder, rescan, adopt it as a draft model — and the M4 printer
flow — flag off (printers 503, app otherwise fine), flip the printer
toggle on, register a printer, probe it (soft-fails, no hardware), and
confirm a bare `.gcode` and a not-ready printer are both rejected before
any print starts — all over HTTP) lives in
`backend/tests_e2e/` and runs via:

```sh
scripts/e2e.sh
```

### Frontend

```sh
cd web
npm install
npm run dev
```

Vite's dev server proxies `/api` to `http://localhost:8080`, so run the
backend (or `docker compose up -d api db redis worker-io worker-cpu`)
alongside it.

```sh
npm run build   # tsc --build + vite build, output to web/dist
npm run test    # vitest
```
