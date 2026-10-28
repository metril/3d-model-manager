# Browser extension — Send to my library

A sideloaded Chrome / Firefox (MV3) extension that saves MakerWorld, Thingiverse, and
Printables model pages straight into your self-hosted 3D Model Manager
library, and keeps the app's stored MakerWorld cookie fresh while you browse
— no more copying cookie values out of DevTools by hand.

## Install (sideload)

This extension isn't published to the Chrome Web Store — sideload it from the
zip attached to each GitHub release:

1. Download `tdmm-extension-<version>.zip` and its `.sha256` sidecar from the
   [latest release](https://github.com/metril/3d-model-manager/releases/latest).
2. Verify it, then unpack:

   ```sh
   sha256sum -c tdmm-extension-<version>.zip.sha256
   unzip tdmm-extension-<version>.zip -d tdmm-extension
   ```

3. Open `chrome://extensions`.
4. Enable **Developer mode** (top right).
5. Click **Load unpacked** and select the unzipped folder (the one containing
   `manifest.json`).

Chrome won't update a "Load unpacked" extension for you — to upgrade, download
the newer release's zip, unpack it over the same folder, and hit **Reload** on
the extension's card in `chrome://extensions`.

### Firefox

Firefox needs Firefox 115 or newer (the manifest sets `strict_min_version`).
An unsigned extension can only be loaded **temporarily**:

1. Unzip the release (or use this `extension/` folder from a checkout).
2. Open `about:debugging#/runtime/this-firefox` and click **Load Temporary
   Add-on…**, then pick the `manifest.json` inside the folder.
3. Temporary add-ons are removed when Firefox restarts; reload it the same
   way. (For a permanent install you would have to sign it with Mozilla; or
   run it with `npx web-ext run` from this folder during development.)

Firefox grants **no** site access by default. After installing, open the
popup or options page and click **Grant access** (it appears whenever your
app's address or the gallery sites are not yet permitted), and accept the
prompt. Chrome grants the gallery sites at install and only asks for your
app's address on **Save**.

**Or, for development**: point **Load unpacked** at this `extension/` folder
directly from a source checkout. Same steps 3-5, no download — and your edits
are live on **Reload**.

### Versioning

The extension shares one version with the app, always. Release `v0.4.0` of
the app ships extension `0.4.0`; there is no independent extension version to
track.

Establishing that cost a **one-time renumber from `0.3.0` down to `0.1.0`**
when the extension moved onto the app's release train. That's a version going
*backwards*, which sounds alarming and isn't: Chrome's downgrade protection
applies to **packed CRX updates delivered through an update URL**, and this
extension is installed unpacked via **Load unpacked**, which performs no
version comparison at all. If you have an older sideload, replace the folder
and reload — nothing will object.

## Configure

1. Open the extension's options page (right-click its toolbar icon → **Options**,
   or `chrome://extensions` → the extension's **Details** → **Extension options**).
2. Enter your app's base URL, e.g. `http://your-host:8080` (no trailing path).
3. Mint an **API token** in the app itself, at **Settings → Accounts →
   Browser extension** (the options page links straight there once you have
   typed your app address), and paste it in. The **Show** button reveals the
   token while you check it.
4. Click **Save** — Chrome prompts for permission to reach your app's
   origin; grant it (the extension only asks for the one origin you gave it,
   nothing broader).
5. Saving (and **Test connection**) runs a diagnostics check against
   `GET /api/ext/me`: it shows which account the token belongs to and the
   app's version, warns if the token lacks the `library:write` or
   `settings:write` permission, and flags a **version mismatch** when the
   app and the extension are on different releases (they ship together, so
   update whichever is older). The last result is shown in the popup's
   footer. (Older servers without `/api/ext/me` fall back to a plain
   connectivity check.)
6. Optional, **Printer key**: see [Send to a printer](#send-to-a-printer).

Use `https://` for any app that isn't on your local network (localhost,
private IPs and `.local` names are fine over `http://`); the options page
warns when your token would otherwise travel unencrypted.

## Usage

- **Toolbar button**: open a model page on MakerWorld, Thingiverse, or
  Printables, click the extension's toolbar icon, then **Save to my
  library**. On any other page the popup links to your library and lists
  the supported sites.
- **Right-click a link**: on any page, right-click a link to a model on one
  of those sites and choose **Save model to my library** from the context
  menu — no need to open the link first.

### Save options

Before saving, the popup offers a **Collection** (one of the app's followed
collections) and **Tags** (type, press Enter or comma; existing tags
autocomplete). They are sent with the import and applied by the app when it
finishes (an already-saved model is not changed). The lists are cached for a
few minutes and your last choice is remembered. The right-click entry saves
without options.

### Import tracking

The background worker, not the popup, follows the import: it polls the app
until the import finishes and keeps the progress in `storage.session`. The
popup subscribes to it and shows live state (waiting, fetching, downloading,
done, or the failure reason, e.g. "Bambu sign-in expired — reconnect your
Bambu account in Settings."), plus **Open in app** (`/models/<id>`) once it
is done — closing and reopening the popup mid-import picks the state back
up. The toolbar badge on that tab shows the result too (`…` working, `✓`
done, `!` failed), for right-click saves as well. A 1-minute alarm resumes
tracking if the worker was restarted; an import still unfinished after 30
minutes is dropped with a "check the app" note.

### Send to a printer

Optional. In the app, create an API key with the `queue` and `read` scopes
(and, if you like, a printer allowlist), paste it into **Printer key** on the
options page, and save — the key is checked against `/api/ext/v2/ping` and
rejected if either scope is missing. Then:

- once an import is **done**, the popup shows a printer select and **Add to
  queue** (the printer you choose is remembered);
- the right-click menu gains **Save & queue to printer**, which queues as
  soon as the import finishes.

Only an already-sliced file (`.gcode.3mf`) can be queued. If the model has
exactly one, it is queued; if it has none the popup says "No sliced file yet
— slice it in the app" (with **Open in app**), and if it has several it asks
you to pick one in the app. A right-click queue that can't complete shows a
`!` badge; open the popup on that tab for the explanation. Clearing the
Printer key removes the menu entry and the popup controls.

## Syncing your MakerWorld collections

MakerWorld blocks servers from listing your named collections (the SSR route
behind them is intermittently Cloudflare-walled from a server IP), and it
also serves each *named* collection's own item list as empty from a server
IP even when the request itself succeeds (the app only ever gets your
"all collected models" aggregate back) — so the app can't enumerate either
your per-collection structure or a named collection's contents on its own.
Your browser, already signed in, can see both just fine. To hand that over,
just **visit your MakerWorld collections page**
(`makerworld.com/@<your handle>/collections`) while signed in — the
extension notices and syncs automatically, no click required.

The extension reads your collections page's *live* data — the same
`/_next/data/...` route the page itself uses to render — rather than trusting
whatever was embedded in the page on its first load, so it still works
correctly if you got to the collections page by clicking around inside
MakerWorld rather than loading it fresh. It syncs *all* of your collections,
including private ones, not just the ones you've made public, and it reads
each collection's own contents the same live way instead of relying on an
endpoint that can come back empty even when you're signed in and looking
right at the page.

That visit pushes your collection list (so a collection you follow by
pasting its URL shows its real title instead of a generic placeholder), then
reads and pushes each collection's contents. It's throttled to only push
when something's actually changed since the last sync, so revisiting the
same unchanged page repeatedly doesn't spam the app. This happens silently
in the background — if it fails (app unreachable, page unreadable, etc.),
nothing is shown in the tab; check the browser's extension console if
collections aren't showing up as expected. Turn it off on the options page
(**Sync collections automatically when you visit your MakerWorld collections
page**) if you'd rather trigger it by hand.

The popup's **Sync collections to app** button still works exactly as
before, as a manual trigger: click the extension's toolbar icon while on
your collections page, then **Sync collections to app**. The status line
tracks progress and finishes with something like "Synced 3 collections (24
items)." If a collection's items couldn't be read from the page, the count
is called out, e.g. "Synced 3 collections (18 items; 1 collection
unreadable)" rather than silently synced empty. Re-run any time your
collections (or their contents) change (or just revisit the page for the
automatic sync to pick it up) — each sync replaces what the app has cached
for a given list with what's on the page, so a deleted/renamed collection or
an added/removed item updates too.

## The MakerWorld courier

While you're signed in and browsing makerworld.com, the extension watches
for your MakerWorld session cookie and, when it changes, pushes the new
value to the app so your followed MakerWorld collections keep syncing
without you ever opening DevTools.

**Honest caveat**: this only happens while your browser is running and you
visit makerworld.com. If the cookie fully expires during a long stretch with
the browser closed (or without a MakerWorld visit), syncing pauses until you
next sign in and browse to makerworld.com — the extension has no way to
refresh a cookie it can't see.

## Running the tests

Needs Node 24 (no dependencies to install). From the repo root:

```sh
node --test extension/test/*.test.js
```

or from inside `extension/`:

```sh
node --test test/*.test.js
```

Use the glob form, not a bare `test` / `extension/test` directory argument --
the directory form is broken on Node 24 (it tries to load the directory as a
module). `test/chromeStub.js` is a minimal in-memory `chrome` stub shared by
the storage, background and options tests. `test/contract.json` lists the
app endpoints the extension may call; `api.test.js` fails if `api.js` calls
anything not in it (the backend asserts the same file).

CI also runs a manifest required-keys check and `npx web-ext@8 lint
--source-dir extension --self-hosted`. Run the lint locally the same way;
the remaining warnings (dual Chrome/Firefox background keys, Firefox for
Android) are expected.

## Look and feel

`src/theme.css` is shared by the popup and the options page. Its tokens
mirror the web app (slate + teal, 0.625rem radius), follow the OS light/dark
setting, and use the system font stack only (extension pages can't load
remote fonts).
