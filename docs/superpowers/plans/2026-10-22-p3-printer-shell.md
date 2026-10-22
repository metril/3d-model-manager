# P3 Printer-first Shell and Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or superpowers:executing-plans) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-centre the app on printing: one `GET /api/printers/overview` round-trip that feeds a printer-first dashboard at `/`. Regroup the nav into Print / Library / Inventory / System with the final IA and redirects, add History and per-printer pages, and fix the `/models/{id}/prints` visibility leak.

**Architecture:** The backend adds a read-only overview service. It makes one Redis `MGET` across the visible printers plus a fixed set of SQL statements (printers, active jobs, queue top 3, last runs via window function, recent 10), so the statement count does not grow with the number of printers. The web side keeps `navItems.ts` as the single nav source (`ALL_NAV_ITEMS`), with dynamic per-printer entries built from `usePrinters()`. Redirects live in route `beforeLoad` hooks that read cached `features`/`printers`/`auth/me` queries. The dashboard is assembled from `PrinterCard` (+`ActiveJobStrip`) and `RecentRunsList` over `usePrintersOverview()`. SSE `print_job.updated` already invalidates the `["printers"]` prefix, which refreshes the overview.

**Tech Stack:** FastAPI + SQLAlchemy async + Redis (backend); React 19 + TanStack Router/Query + Tailwind v4 + shadcn + Vitest (web).

**Spec:** `docs/superpowers/specs/2026-10-21-bambuddy-parity-design.md` §P3 (hooks for P4/P5/P6/P7/P8 noted inline).

**Branch:** `p3-printer-shell` from `main`. Leave `compose.yaml` (uncommitted local change) alone. Agents never `git stash`/`checkout`; cap pytest with `timeout 600`.

## Global Constraints

- Commit: `git -c user.name=metril -c user.email=1517921+metril@users.noreply.github.com commit -m "<type(scope): subject>"`. Do not mention AI or add trailers.
- Web gates: `cd web && npx vitest run <file>`, `npm run build` (tsc -b; `tsc --noEmit` is a no-op here), `npx oxlint` (no new warnings vs baseline). Backend: `cd backend && timeout 600 uv run pytest tests/<file> -q`, `uv run ruff check . && uv run ruff format --check .`.
- Routes (final): `/` dashboard · `/print/queue` · `/print/history?tab=runs|stats` (default `runs`) · `/printers/$id` · `/library` (search = `parseLibrarySearch`) · `/collections` · `/add` · `/duplicates` · `/inventory/spools` · `/inventory/materials` · `/inventory/profiles` · `/system/jobs` · `/settings` · `/models/$slug`.
- Redirects: `/dashboard`→`/print/history?tab=stats`; `/queue`→`/print/queue`; `/jobs`→`/system/jobs`; `/printer`→`/printers/$id` when exactly 1 visible printer, else `/`; `/`→`/library` (search params preserved) when `printer_enabled=false`, OR when the user has 0 visible printers and lacks `settings:write`. An admin with 0 printers stays on `/` and sees the `PrinterSetupCard` empty state.
- Overview query key `["printers","overview"]`; poll `2500` ms while any printer `gcode_state ∈ {RUNNING,PREPARE,PAUSE}`, else `8000`.
- Tokens (`index.css`, light+dark): `--state-printing: var(--primary)` (amber `oklch(0.646 0.176 41.1)`), `--state-finished: oklch(0.627 0.17 149)`, `--state-failed: var(--destructive)`, `--state-paused: oklch(0.554 0.046 257)` (slate), `--state-idle: var(--muted-foreground)`. Expose them as Tailwind colours `state-printing|finished|failed|paused|idle`.
- Density utilities: `label-caps` = 12px / uppercase / `tracking-[0.06em]` / muted; `data-table` = 13px body, 28px rows (`h-7`), `tabular-nums`; panels use 1px `border-border`, `rounded-[6px]`, no `shadow-*`. Units are wrapped in `<span class="text-muted-foreground">`. Amber appears only on the primary action and in-progress state.
- No chart library. `Sparkline` is inline SVG.
- Visual tasks (4, 7, 8, 9, 10) must invoke the `frontend-design` skill before writing UI.
- Slots for later phases render nothing in P3 (no dead controls): `PrinterCard.badge?: ReactNode` (P8 HA alert), `PrinterCard.camera?: ReactNode` (P4 `CameraTile`; default is a 16:9 placeholder), and `PrintDashboardPage` `railExtras` (P7 review inbox). Finance is omitted from the nav until P7.

## Review Focus

1. Overview visibility: non-admins see only ACL printers, and runs/active jobs from hidden printers never leak (Task 1 `test_overview_hides_invisible_printers`).
2. Statement count stays constant for 1 vs 3 printers (Task 1 `test_overview_constant_query_count`).
3. Manual runs are kept and auto runs from hidden printers are dropped on `/models/{id}/prints` (Task 2 `test_model_prints_hides_auto_runs_from_invisible_printers`).
4. The redirect matrix (flag off / 0 printers non-admin / admin empty / single-printer `/printer` / legacy paths) (Task 5 `routes.test.tsx`).
5. Pause/stop show only when `canPrinter(id,"control")` holds, and an SSE `print_job.updated` refreshes the card (Task 7 `PrinterCard.test.tsx`, Task 8 `PrintDashboardPage.test.tsx`).

---

### Task 1: `GET /api/printers/overview`

**Files:** Create `backend/app/services/printer_overview.py`, `backend/tests/test_printer_overview_api.py`. Modify `backend/app/schemas/printers.py`, `backend/app/api/printers.py` (route declared **before** `/{printer_id}`), `backend/app/api/archives.py` (rename `_select`/`_out` → public `archive_select`/`archive_out` and update callers).

**Interfaces:**
```python
class ActiveJobOut(PrintJobOut):  # + resolved via file->revision->model, nullable
    plate: int | None; model_id: int | None; model_slug: str | None; model_name: str | None
class PrinterOverviewItem(BaseModel):
    printer: PrinterOut; status: PrinterStatusOut; active_job: ActiveJobOut | None
    last_runs: list[ArchiveOut]  # <=5, newest first
    can_print: bool; can_control: bool
class PrintersOverviewOut(BaseModel):
    printers: list[PrinterOverviewItem]   # ordered by name
    up_next: list[QueueEntryOut]          # first 3 of the global queue (queue has no printer_id until P6)
    recent_runs: list[ArchiveOut]         # <=10 across visible printers
async def build_overview(db, redis, settings, ctx: AuthContext) -> PrintersOverviewOut
```
Router `GET /printers/overview` (inherits `require_printer_enabled`). Active job = latest `print_jobs` row per printer with state in `queued|uploading|starting|printing|paused` (`DISTINCT ON (printer_id)`). Last runs use `row_number() over (partition by printer_id order by finished_at desc nulls last, id desc) <= 5`. Redis is read with one `MGET` of `state_key(id)`, and corrupt/absent values give `online=False`. Add `queue_service.list_queue(db, settings, limit: int | None = None)`.

- [ ] Write failing tests (printer fixtures + `make_user`/`make_group`/`login_as` from `tests/auth_fixtures.py`; seed state via `redis_client.set(state_key(pid), json.dumps({...}))`):
```python
async def test_overview_shape(authenticated_client, printer_enabled, redis_client): ...  # 2 printers, one RUNNING w/ job+archives -> keys, status.online, active_job.layer, len(last_runs)<=5, up_next<=3
async def test_overview_hides_invisible_printers(make_user, make_group, login_as, ...): ...  # member w/ ACL on p1 only -> printers==[p1]; recent_runs printer_ids ⊆ {p1}; can_control reflects ACL
async def test_overview_constant_query_count(authenticated_client, printer_enabled, db_engine): ...  # count before_cursor_execute for 1 vs 3 printers -> equal
async def test_overview_503_when_disabled(authenticated_client): ...
```
- [ ] `timeout 600 uv run pytest tests/test_printer_overview_api.py -q` and confirm it fails (404/422).
- [ ] Implement the schemas, service and route, then update `test_auth_sweep.py` if needed (no new path params; `/printers/overview` must not be shadowed by `{printer_id}`).
- [ ] Run the new file plus `test_printers_api.py test_archives_api.py test_queue_api.py test_auth_sweep.py test_perm_sweep.py` and ruff, and confirm they pass.
- [ ] Commit `feat(printers): add per-user printers overview endpoint`.

### Task 2: Visibility fix for `GET /models/{id}/prints`

**Files:** Modify `backend/app/api/prints.py`, `backend/app/services/prints.py`, `backend/tests/test_prints_api.py`.

**Interfaces:** `prints_service.list_prints(db, model_id, visible_printer_ids: set[int] | None)` keeps rows where `source == manual` OR `visible is None` OR the joined `print_jobs.printer_id ∈ visible`. The route adds `ctx: AuthContext = Depends(require_session)`.

- [ ] Failing test `test_model_prints_hides_auto_runs_from_invisible_printers`: seed 1 manual print, 1 auto print on visible p1 and 1 auto print on hidden p2. A member with an ACL on p1 sees 2 rows (manual + p1), and an admin sees 3.
- [ ] Run and confirm it fails (member sees 3).
- [ ] Implement with an outer join on `PrintJob` via `Print.print_job_id`.
- [ ] Run `test_prints_api.py test_auth_sweep.py` and confirm they pass.
- [ ] Commit `fix(prints): hide auto runs from printers the caller cannot see`.

### Task 3: Overview client + state vocabulary

**Files:** Modify `web/src/api/types.ts`, `web/src/api/printers.ts`, `web/src/api/queue.ts` (each mutation also invalidates `["printers","overview"]`), `web/src/api/archives.ts` (`usePatchRun` invalidates the overview too). Create `web/src/lib/printerState.ts`, `web/src/lib/printerState.test.ts`, `web/src/api/printers.test.ts` (extend).

**Interfaces:**
```ts
export interface ActiveJobOut extends PrintJobOut { plate: number|null; model_id: number|null; model_slug: string|null; model_name: string|null }
export interface PrinterOverviewItem { printer: PrinterOut; status: PrinterStatusOut; active_job: ActiveJobOut|null; last_runs: ArchiveOut[]; can_print: boolean; can_control: boolean }
export interface PrintersOverviewOut { printers: PrinterOverviewItem[]; up_next: QueueEntry[]; recent_runs: ArchiveOut[] }
export function overviewRefetchInterval(d?: PrintersOverviewOut): number  // 2500 | 8000
export function usePrintersOverview(opts?: { enabled?: boolean })
export type StateTone = "printing" | "finished" | "failed" | "paused" | "idle" | "offline"
export function toneForGcode(online: boolean, gcode: string|null): { tone: StateTone; label: string }
export function toneForResult(r: ArchiveResult): StateTone
```
- [ ] Write failing tests: the interval table; `toneForGcode` (RUNNING/PREPARE→printing, PAUSE→paused, FINISH→finished, FAILED→failed, IDLE→idle, offline); `toneForResult`; and that `usePrintersOverview` GETs `/printers/overview`.
- [ ] Run `npx vitest run src/lib/printerState.test.ts src/api/printers.test.ts` and confirm failure.
- [ ] Implement.
- [ ] Run those files plus `src/hooks/useEvents.test.tsx`. Add a case asserting that `print_job.updated` invalidates a cached `["printers","overview"]` query. Confirm pass, then `npm run build`.
- [ ] Commit `feat(web): add printers overview hook and state tone mapping`.

### Task 4: Tokens and density primitives (`frontend-design`)

**Files:** Modify `web/src/index.css`. Create `web/src/components/ui/stat-tile.tsx`, `stat-tile.test.tsx`, `sparkline.tsx`, `sparkline.test.tsx`, `state-badge.tsx`, `state-badge.test.tsx`.

**Interfaces:**
```ts
StatTile({ label, value, unit?, delta?, children? /* e.g. <Sparkline/> */, className? })
Sparkline({ values: number[]; width?=96; height?=24; tone?: StateTone|"primary"; "aria-label": string })  // inline SVG polyline; <2 points -> flat line
StateBadge({ tone: StateTone; label: string; pulse?: boolean })  // dot + label-caps text; colour from --state-* var
```
- [ ] Write failing tests: StatTile renders the label in `label-caps` with the unit muted; Sparkline renders a `polyline` with `points` normalised to the height (e.g. `[0,5,10]`) and a flat line for `[]`; StateBadge `tone="failed"` uses `bg-state-failed`.
- [ ] Run them and confirm failure.
- [ ] Add the tokens from Global Constraints (`:root` + `.dark`, `@theme inline` colour mappings) and the `label-caps`/`data-table` `@utility` blocks, then implement the components.
- [ ] Run them and confirm pass, then `npm run build` and `npx oxlint`.
- [ ] Commit `feat(ui): add state tokens, StatTile, Sparkline and StateBadge`.

### Task 5: Routes, redirects and page shells

**Files:** Modify `web/src/routes.tsx`, `web/src/api/features.ts` (export `featuresQueryOptions` if missing), `web/src/pages/SettingsPage.tsx` (drop the Materials tab and its test cases), every library link that targets `"/"` (`AppSidebar.tsx` collections/categories, `CommandPalette.tsx`, `ProvenanceBlock.tsx`, `ModelHeader.tsx` post-delete, `AddPage.tsx`, `LibraryPage.tsx` navigate calls + test), and `/jobs` links (`AppSidebar` ScanChip, `StorageLocationBar.tsx`+test, `LibraryPage.tsx` toast). Create `web/src/routes.test.tsx`, `pages/PrintDashboardPage.tsx`, `pages/PrintHistoryPage.tsx` (temporarily renders the existing `DashboardPage` content under `tab=stats` and `PrintJobHistory` under `runs`), `pages/PrinterDetailPage.tsx` (temporarily renders `PrinterStatusPanel` for `$id`), `pages/MaterialsPage.tsx` (PageContainer + `MaterialsSection`), and `pages/PlaceholderPage.tsx`, which replaces `ComingSoonPage.tsx` (delete it): `PlaceholderPage({ title, phase: string, blurb })` → "Coming in a later phase" card.

**Interfaces:** `parsePrintHistorySearch(s): { tab: "runs" | "stats" }`. Index route `beforeLoad` → `redirectForIndex({ features, printers, me, search })` exported from `routes.tsx` as a pure function returning a redirect target or `null`. `printerRedirect(printers)` for `/printer`. `/` redirects keep the library search keys.

- [ ] Write failing `routes.test.tsx`: drive `createRouter({ routeTree, history: createMemoryHistory })` with `api.get` mocked, covering:
  - flag off `/` → `/library`
  - member with 0 printers → `/library`
  - admin with 0 printers stays on `/`
  - 1 printer stays on `/`
  - `/?collection=3` with flag off → `/library?collection=3`
  - `/dashboard` → `/print/history?tab=stats`
  - `/queue`, `/jobs` redirect to their new paths
  - `/printer` with 1 printer → `/printers/7`, with 2 printers → `/`

  Also add unit cases for `redirectForIndex`.
- [ ] Run `npx vitest run src/routes.test.tsx` and confirm failure.
- [ ] Implement the routes and shells, then migrate links. `/system/jobs` renders `JobsPage`, `/print/queue` renders `QueuePage`, `/inventory/spools` renders `PlaceholderPage` (phase "P5"), and `/inventory/profiles` renders `PlaceholderPage` (phase "P6").
- [ ] Run `npx vitest run` (whole suite: route-path-sensitive tests like `CollectionsPage`/`LibraryPage`/`StorageLocationBar`/`SettingsPage` must pass), then `npm run build`.
- [ ] Commit `feat(web): move to print-first routes with legacy redirects`.

### Task 6: Nav groups, sidebar, top bar, printer widget

**Files:** Modify `web/src/components/shell/navItems.ts`, `navItems.test.ts`, `AppSidebar.tsx`, `AppSidebar.test.tsx`, `TopBar.tsx`, `SidebarPrinterWidget.tsx`.

**Interfaces:**
```ts
interface NavItem { to; label; icon; featureGated?; perm?: Perm; params?: Record<string,string>; exact?: boolean }
interface NavGroup { label: "Print"|"Library"|"Inventory"; items: NavItem[]; featureGated?: boolean }
NAV_GROUPS  // Print{gated}: Dashboard "/"(exact), Queue, History · Library: Models "/library", Collections, Add (perm library:write), Duplicates (perm library:write) · Inventory: Spools, Materials (perm inventory:write), Profiles & Recipes
SYSTEM_ITEMS: NavItem[]  // Background Jobs "/system/jobs", Settings "/settings" (Finance added in P7)
ALL_NAV_ITEMS = [...groups (gated flag copied onto items), ...SYSTEM_ITEMS]
printerNavItems(printers: PrinterOut[]): NavItem[]  // to "/printers/$id", params {id}, label printer.name, featureGated
filterNavGroups(groups, { printerEnabled, can }): NavGroup[]  // drops gated groups/items, empty groups
```
The sidebar inserts `printerNavItems(usePrinters({enabled: printerEnabled}).data)` after History in Print, pins `SYSTEM_ITEMS` at the bottom (replacing `SETTINGS_ITEM`), and keeps the failed-imports badge on Collections. The "Add to library" button stays. `TopBar` breadcrumb: `/printers/<id>` → `Print / <name>`, `/models/<slug>` → `Library / <slug>`, else `NAV_LABELS`. `SidebarPrinterWidget` stays compact: it links to `/printers/$id`, uses `toneForGcode` and `StateBadge` dot colours, and shows a `+N` suffix when there is more than one printer (it still shows only the first printer).

- [ ] Rewrite failing `navItems.test.ts`:
  - group labels `["Print","Library","Inventory"]` and System last
  - with `printerEnabled:false` the Print group disappears
  - perm gating hides Duplicates/Add/Materials
  - `printerNavItems` builds `/printers/$id` + params
  - every `ALL_NAV_ITEMS` path is unique and has a `NAV_LABELS` entry

  In `AppSidebar.test.tsx`, cover a per-printer link for 2 printers, System pinned, and no Finance.
- [ ] Run and confirm failure.
- [ ] Implement.
- [ ] Run `src/components/shell` tests and confirm pass, then `npm run build`.
- [ ] Commit `feat(shell): regroup nav into print, library, inventory and system`.

### Task 7: `PrinterCard` + `ActiveJobStrip` (`frontend-design`)

**Files:** Create `web/src/components/print/PrinterCard.tsx`, `PrinterCard.test.tsx`, `ActiveJobStrip.tsx`, `ActiveJobStrip.test.tsx`.

**Interfaces:**
```ts
PrinterCard({ item: PrinterOverviewItem; camera?: ReactNode; badge?: ReactNode })
// camera: aspect-video tile (default placeholder: muted bg + printer glyph) with StateBadge overlaid top-left; badge slot top-right
// body: ActiveJobStrip | "Idle" line; SpecRow nozzle/bed "215 / 220 °C" (units muted); AMS chips via FilamentChip from status.trays; header links to /printers/$id
ActiveJobStrip({ job: ActiveJobOut; status: PrinterStatusOut; printerId: number; canControl: boolean })
// thumb (model cover via model_slug, else glyph), name (model_name ?? subtask_name), "Plate N", 2px amber progress bar,
// "Layer N/M" + mono ETA (mc_remaining_time -> "1h 05m"), Pause/Resume + Stop (usePrinterCommand) only when canControl; Stop confirms via dialog
```
- [ ] Write failing tests:
  - RUNNING renders progress `aria-valuenow=42`, `Layer 10/120`, `ETA 1h 05m` and the amber `bg-state-printing` bar
  - `canControl=false` gives no Pause/Stop buttons, while `true` gives Pause, which POSTs `/printers/1/pause`
  - offline shows the "Offline" badge
  - trays render chips
  - `badge` slot renders
- [ ] Run and confirm failure.
- [ ] Implement. `canControl` comes from `item.can_control` (server-computed, equivalent to `canPrinter(id,"control")`).
- [ ] Run them and confirm pass, then `npm run build` and `npx oxlint`.
- [ ] Commit `feat(print): add PrinterCard and ActiveJobStrip`.

### Task 8: `PrintDashboardPage` + `RecentRunsList` (`frontend-design`)

**Files:** Create `web/src/components/print/RecentRunsList.tsx`, `RecentRunsList.test.tsx`, `web/src/components/print/UpNextList.tsx`, `UpNextList.test.tsx`, `web/src/pages/PrintDashboardPage.test.tsx`. Fill `web/src/pages/PrintDashboardPage.tsx`.

**Interfaces:**
```ts
RecentRunsList({ runs: ArchiveOut[]; limit?=10 })  // row: result dot (toneForResult), name, printer, grams (Σ est_filament or run.filament_g), cost (run.total_cost), inline Good/Reject (usePatchRun) when run && can("library:write"); row click opens RunDetailSheet
UpNextList({ entries: QueueEntry[] })  // up/down via useMoveQueueEntry, "Print…" opens SendToPrinterDialog when printable_file && any can_print; footer link "All queue" -> /print/queue
PrintDashboardPage()  // usePrintersOverview(); grid cols: 1 printer=1, 2=2, >=3 = lg:grid-cols-3; right rail (xl:w-80): UpNextList, RecentRunsList, ...railExtras (empty in P3)
// 0 printers -> PrinterSetupCard (admin only reachable, per redirect rule)
```
- [ ] Write failing tests:
  - 0 printers shows `PrinterSetupCard`
  - 1 printer gives 1 card and no `grid-cols-2`
  - 2 printers give 2 cards with `md:grid-cols-2`; shapes mirror FakeAdapter states (`{gcode_state:"RUNNING",mc_percent:42,layer_num:10,total_layer_num:120,trays:[…]}` and `{gcode_state:"IDLE"}`)
  - SSE: dispatch a `print_job.updated` through the `useEvents` mock EventSource; the second `/printers/overview` response has `mc_percent:80`, and the card shows `80%`
  - Good click → PATCH `/prints/{id}` `{quality:"good"}`
  - reorder → PATCH queue position
- [ ] Run and confirm failure.
- [ ] Implement.
- [ ] Run these plus `src/pages/QueuePage.test.tsx` and confirm pass, then `npm run build`.
- [ ] Commit `feat(print): build printer-first dashboard`.

### Task 9: `PrintHistoryPage` (runs + stats tabs) (`frontend-design`)

**Files:** Fill `web/src/pages/PrintHistoryPage.tsx`. Create `web/src/pages/PrintHistoryPage.test.tsx` (port the `DashboardPage.test.tsx` cases), `web/src/components/print/HistoryStats.tsx` (moved `DashboardContent`, rebuilt on `StatTile`), `web/src/lib/runSeries.ts`+test. Delete `pages/DashboardPage.tsx` and `DashboardPage.test.tsx`. Modify `components/printer/PrintJobHistory.tsx` (+`printerId?: number` prop forwarded to `useArchives`, `data-table` density) and its test.

**Interfaces:** `dailyRunCounts(archives: ArchiveOut[], days=14, now=new Date()): number[]`. The tabs are synced to `?tab=` via `navigate({ search })`. The stats tab gets a "Runs (14d)" `StatTile` with a `Sparkline` from `dailyRunCounts(useArchives().data)`. The runs tab is hidden (stats only) when `printer_enabled=false`. Links to `/jobs` become `/system/jobs`.

- [ ] Write failing tests:
  - `?tab=stats` renders the stat tiles (ported assertions)
  - the default tab is `runs` and renders the archive table
  - switching tab updates the URL
  - the flag off hides the runs tab
  - `dailyRunCounts` buckets by local day
  - `PrintJobHistory printerId=2` requests `/archives?printer=2`
- [ ] Run and confirm failure.
- [ ] Implement and delete the old page.
- [ ] Run `src/pages/PrintHistoryPage.test.tsx src/components/printer src/lib/runSeries.test.ts` and confirm pass, then `npm run build`.
- [ ] Commit `feat(print): add history page with runs and stats tabs`.

### Task 10: `PrinterDetailPage` (`frontend-design`)

**Files:** Fill `web/src/pages/PrinterDetailPage.tsx`. Create `PrinterDetailPage.test.tsx`. Modify `components/printer/PrinterStatusPanel.tsx`+test (adopt `StateBadge`/`SpecRow`/`label-caps`, controls gated by `canPrinter(id,"control")` as today). Delete `pages/PrinterPage.tsx` and move its disabled-flag card into `PrinterDetailPage`.

**Interfaces:** `PrinterDetailPage()` reads `$id` via `getRouteApi("/authenticated/printers/$id")` and composes:
- left: a large camera placeholder (16:9; the P4 `CameraTile` slot) plus `PrinterStatusPanel`
- right: printer-scoped up-next (from the overview `up_next`), then `PrintJobHistory printerId={id}`

An unknown or invisible id renders a "Printer not found" card. The flag off renders the "Printer integration is off" card.

- [ ] Write failing tests: a valid id renders the name, status and scoped history request; an unknown id gives not-found; the flag off gives the disabled card; a member without control sees no Pause.
- [ ] Run and confirm failure.
- [ ] Implement.
- [ ] Run `src/pages/PrinterDetailPage.test.tsx src/components/printer` and confirm pass, then `npm run build`.
- [ ] Commit `feat(print): add per-printer detail page`.

### Task 11: Command palette, docs, full gates

**Files:** Modify `web/src/components/shell/CommandPalette.tsx`+test, `README.md` / `docs/` nav references (grep `/dashboard`, `/printer`, `/queue`, `/jobs`), and `docs/superpowers/specs/2026-10-21-bambuddy-parity-design.md` only if a P3 statement became untrue (record the chosen resolutions as a short "P3 notes" bullet).

**Interfaces:** Palette sections:
- **Pages**: `filterNavItems(ALL_NAV_ITEMS)`
- **Printers**: `printerNavItems(...)` when enabled
- **Collections / Categories / Models**: unchanged, but targets become `/library`
- **Actions**: "Open print queue" and "Run history stats" → `/print/history?tab=stats`

- [ ] Write failing tests: the palette lists one entry per printer by name, contains no `/dashboard`, and selecting a collection navigates to `/library?collection=`.
- [ ] Run and confirm failure.
- [ ] Implement and update the docs.
- [ ] Full gates: `cd web && npx vitest run && npm run build && npx oxlint`; `cd backend && timeout 600 uv run pytest -q -x && uv run ruff check . && uv run ruff format --check .`. All must pass.
- [ ] Commit `feat(shell): update command palette for print-first IA`.

---

## Self-review / pre-flight (resolved inline)

- **Spec coverage:** overview (T1), prints fix (T2), nav groups/`ALL_NAV_ITEMS`/dynamic printers (T6), redirects (T5), new pages (T5 shells → T8/T9/T10), PrinterCard/ActiveJobStrip/RecentRunsList (T7/T8), StatTile/Sparkline + tokens/density (T4), CommandPalette (T11), and the TopBar/SidebarPrinterWidget/PrinterStatusPanel modifications (T6/T10). Tests cover navItems, redirects, 0/1/2 printers, SSE, and the Dashboard/Queue/Printer test updates (T5/T8/T9/T10).
- **Typed-route ordering:** routes (T5) land before navItems (T6) because `NavItem.to` is typed against the route tree.
- **Shared files:**
  - `routes.tsx` changes only in T5.
  - `PrintHistoryPage.tsx`/`PrinterDetailPage.tsx`/`PrintDashboardPage.tsx` are shells in T5 and filled in T9/T10/T8.
  - `PrintJobHistory.tsx` changes only in T9 (T10 consumes its `printerId`, so T9 comes before T10).
  - `AppSidebar.tsx` link edits happen in T5 (paths) and T6 (structure), sequentially.
  - `api/archives.ts`/`queue.ts` invalidation lands in T3, before its consumers (T8).
  - Backend `archives.py` rename (T1) has no other P3 user.
- **Names are consistent across tasks:** `usePrintersOverview`, `PrinterOverviewItem`, `toneForGcode`, `StateBadge`, `printerNavItems`, `filterNavGroups`, `SYSTEM_ITEMS`, `parsePrintHistorySearch`, `redirectForIndex`.
