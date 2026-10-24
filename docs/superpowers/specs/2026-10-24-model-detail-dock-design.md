# Model detail dock (dockable panels + live pop-outs)

Date: 2026-10-24. Scope: web only (`web/src/components/model-detail/dock/`).

## Intent

The model detail page (viewer + ten cards) becomes a dockable workspace:
drag tabs between groups, resize, close/re-add, and pop any group into its own
browser window that stays live (same React tree, same React Query cache). The
layout persists per browser. Below 900px the page keeps today's stacked layout.

## Library

`dockview-react@8.6.1` (zero deps, React 19 ok). Native `api.addPopoutGroup`,
`toJSON`/`fromJSON`, `--dv-*` theming, `renderer: "always"`.

## Spike results (decided, not to be re-run)

- Native `addPopoutGroup(group, { popoutUrl: "/popout.html" })` works; main-window
  CSS is copied into the popout automatically.
- Moving a panel between groups/windows does NOT remount its React component, so
  viewer state (camera, selection, tools) survives pop-out and re-dock.
- Canvas resize works in a popout without keying the `<Canvas>` and without a
  ResizeObserver polyfill.
- The one required viewer change: R3F's loop uses the main window's rAF, so a
  popped-out canvas freezes whenever the main window is hidden or minimised.
  Fix: `frameloop="never"` + `RafDriver` that overrides the store's `invalidate`
  to schedule `advance(ts / 1000, true)` on
  `gl.domElement.ownerDocument.defaultView.requestAnimationFrame`, re-targeting
  when the canvas changes window. Scheduling lives in `viewer/rafDriver.ts`
  (unit tested); the driver subscribes to a relocate emitter
  (`lib/windowRelocate.tsx`) fired from dockview's panel `onDidLocationChange`
  and `onDidAdd/RemovePopoutGroup`. No emitter provider (the `/viewer/$slug`
  window page) means the subscription is a no-op.
- `/viewer/$slug` and the viewer's "Open in window" button are unchanged.

## Panels

Ids: `viewer, description, tagsLinks, related, gcodeProfiles, printHistory,
filesDocs, revisions, notes, printTips, specs` (`panelMeta.ts`, pure data).
`PanelContent.tsx` maps each to a thin adapter that reads `ModelDetailContext` (registry in `panels.ts`)
(`model, editMode, relocateOpen, studio`); card signatures are unchanged. The
page still owns the slug gate, fetch and edit-mode reset, then provides the
context. `filesDocs` passes `studio.onViewIn3D`, so "View in 3D" from a popped-out
Files panel drives the main-window viewer through the shared studio state.

## Default layout (`defaultLayout.ts`)

Left (~62%): viewer over one tabbed group (Description | Tags & links | Related).
Right: two stacked tabbed groups, cards in today's order (G-code profiles | Print
history | Files) over (Revisions | Notes | Print tips | Specs). Decision: seven
stacked single-tab groups left each card ~100px tall in a viewport-height dock, so
the right column is tabbed instead; users can still split any card out.
Closed panels re-add via the header "Panels" menu next to a sibling in their
default group, else beside the neighbouring default group. "Reset layout" clears
storage and rebuilds the default.

## Persistence (`layoutStore.ts`)

Key `tdmm.modelDetailLayout.v1` = `{ v: 1, known: PanelId[], layout }`
(`layout` = dockview `toJSON`). `load` validates version, structure and that every
panel id is in the registry; anything else falls back to the default. `known`
records registry ids at save time so panels added later are appended on load while
panels the user closed stay closed. Saves are debounced 300ms and flushed on
unmount. Popouts never persist: the browser closes them on reload, so `save`
folds popped-out panels back into their docked group and un-hides dockview's
hidden placeholder group.

## Pop-out mechanics

- Entry points: per-group header button, tab context menu ("Pop out"). The
  popout's header button becomes "Return to dock" (closes the window; dockview
  re-docks). Blocked pop-ups raise the toast "Allow pop-ups to open panels in a
  window".
- `web/public/popout.html` is a bare same-origin page; the theme class is applied
  to the dock via dockview's `theme` option (otherwise dockview applies its dark
  default and shadows our variables). `dockTheme.css` maps `--dv-*` to app tokens.
- Theme sync: on popout add, copy `<html>` class, `color-scheme` and
  `data-theme/accent/style` into the popout and keep them synced with a
  MutationObserver on the main `<html>`; disconnected on remove/unmount.
- Radix overlays: `lib/portalContainer.tsx` provides the hosting panel's
  `ownerDocument.body` (undefined = main body). The shared `ui/` dropdown, select,
  popover, dialog, sheet and tooltip wrappers default their portal to it (an
  explicit `container` prop still wins, e.g. the fullscreen viewer), so menus
  opened in a popout render in the popout.

## Mobile (<900px)

`useMediaQuery("(min-width: 900px)")` in the page. Narrow: `StackedPanels`
renders panel bodies in today's order through `DetailLayout`; no dock, no Panels
menu. jsdom (no `matchMedia`) renders stacked.

## Out of scope / seam

- Floating groups, multi-window layouts persisted across reload.
- Dashboard dock: generalise `ModelDetailDock` to `<PageDock registry storageKey
  defaultBuilder>` so the print dashboard's main column + rail can adopt it.
- Per-model layouts (layout is global per browser).
