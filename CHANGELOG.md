# Changelog

## [0.3.0](https://github.com/metril/3d-model-manager/compare/v0.2.0...v0.3.0) (2026-09-13)


### Features

* **r13a:** GyroidVault-style detail layout and viewer dock ([9b732c8](https://github.com/metril/3d-model-manager/commit/9b732c8d2076b9e9050a5797854e73f03cfe49cf))
* **r13b:** library grid/list/folder views, categories, new sorts, scan button ([34ac797](https://github.com/metril/3d-model-manager/commit/34ac797e4893e6828950a3788186860325aa7529))
* **r13c:** materials, custom metadata, print tips, doc files, dashboard lists, printer build volume ([6b1f935](https://github.com/metril/3d-model-manager/commit/6b1f935977a94a03ed75c710d738460517b8d6b4))


### Bug Fixes

* **r13a:** review fixes — View in 3D hand-off, sheet title, snapshot cover replace, layout stretch, dock wrap ([9e6d063](https://github.com/metril/3d-model-manager/commit/9e6d0639e1a40dc808e4f8540462c468ce0f4d1f))
* **r13b:** palette category colors, drillable storage tree with files, sidebar filter sync, storage invalidation ([0867fc9](https://github.com/metril/3d-model-manager/commit/0867fc946f06d8ee9b4f603b5b86a97ae12e0fea))
* **r13c:** docs out of Files tab, dashboard keys, metadata limits, migration downgrade safety, build-volume backfill/reseed, doc intake guard ([43dc41b](https://github.com/metril/3d-model-manager/commit/43dc41bcba0aa3d1da9894927677a36765bf600a))

## [0.2.0](https://github.com/metril/3d-model-manager/compare/v0.1.0...v0.2.0) (2026-09-12)


### Features

* **api:** expose dims_mm, best_slicer_file, printable_file on ModelSummary ([5aeb17c](https://github.com/metril/3d-model-manager/commit/5aeb17c5b7c49edae25c947f8f5409622e064537))
* R12 studio UI rework ([24c9cc7](https://github.com/metril/3d-model-manager/commit/24c9cc7e26355e2524ccec74ace3f310b94b0380))
* **web:** grouped collapsible sidebar, top bar, and Ctrl+K command palette ([55cd100](https://github.com/metril/3d-model-manager/commit/55cd100ccf5eac82294bf82fba725c1911a44a83))
* **web:** implicit multi-select, hover dims, and card quick actions in the library ([0f43f58](https://github.com/metril/3d-model-manager/commit/0f43f5814ee7ba96d49a04d206bd76cef6940f92))
* **web:** model detail studio workspace with file rail and side panel ([6420455](https://github.com/metril/3d-model-manager/commit/64204557e0429898d64370ec88dbb5799a0e1c59))


### Bug Fixes

* **api:** card file picks carry blob meta; dims_mm falls back to mesh when slicer file has none ([0506a99](https://github.com/metril/3d-model-manager/commit/0506a99141f34c9b6b14e1eb65402d34a6e03655))
* **api:** pass settings to gallery aggregates from collection previews ([a24d337](https://github.com/metril/3d-model-manager/commit/a24d33701824d75f2eb6b8245bbed7b9008f050b))
* **web:** parts count + All/None in the file rail; modifier hotkeys work inside inputs ([eeff8ed](https://github.com/metril/3d-model-manager/commit/eeff8edda34d04308bd30999264a23a2265a8a9c))
* **web:** rich-text descriptions, off-canvas mobile sidebar, studio overflow, printer poll guard ([59533b8](https://github.com/metril/3d-model-manager/commit/59533b89341f556cca0e8efbc04e3bccc5fd22de))
* **web:** wrap sidebar in TooltipProvider so the collapsed rail doesn't crash ([9421ea2](https://github.com/metril/3d-model-manager/commit/9421ea2870c7d234245f38e54eb07b0286abf84a))

## 0.1.0 (2026-09-12)


### Features

* "Open in slicer" split button for slicer deep links ([19af4ea](https://github.com/metril/3d-model-manager/commit/19af4eaca7743286783f5255cdd75ebce666382d))
* add dashboard page with stats overview ([7ac3e49](https://github.com/metril/3d-model-manager/commit/7ac3e49ab60a666f4d6a2f57ddae4b3ec8ae2fb3))
* add GET /api/stats dashboard aggregate endpoint ([af5d5f6](https://github.com/metril/3d-model-manager/commit/af5d5f674c619fec9918f476a2eda3627aa0139e))
* add print cost estimate (filament + machine time rates) ([b105e8f](https://github.com/metril/3d-model-manager/commit/b105e8f7aea12536dd3f9f499bdb5a7337c68713))
* add useHotkeys hook for document-level keyboard shortcuts ([f9e9a7f](https://github.com/metril/3d-model-manager/commit/f9e9a7ff7c3993d1fcdfc273d6c3660fc2d3d867))
* **api:** add GET .../zip download routes for models and collections ([d00e26a](https://github.com/metril/3d-model-manager/commit/d00e26a5d18a6c8dc7059c78c3970c41e37cf623))
* **backend:** parse Prusa/Orca/Bambu gcode comment metadata (R10-B) ([f39b626](https://github.com/metril/3d-model-manager/commit/f39b626760f91442c0443525a9e139ec8dad63cc))
* **backend:** wire gcode layer metadata into extraction + gcode download ([dc5c348](https://github.com/metril/3d-model-manager/commit/dc5c348c6774f4da53a802dff5061673fcf38d96))
* collection collages (2x2 member-model thumbnail grid) ([8d22938](https://github.com/metril/3d-model-manager/commit/8d22938a93e74b0d889529f0f42a1545413f4de9))
* colored tags (palette key on Tag, chips + color picker in UI) ([244761b](https://github.com/metril/3d-model-manager/commit/244761b55ead1149fdd36b06ff67dd4a8dee3d9d))
* ctrl/cmd/shift+click range select in the library grid ([fe939ec](https://github.com/metril/3d-model-manager/commit/fe939ec9fdc39b0e3454452743c90860411ab207))
* GitHub Actions CI/CD, GHCR publishing, and automated releases ([4011ab9](https://github.com/metril/3d-model-manager/commit/4011ab90fa43faee60283dad584b6c2652231de8))
* GitHub Actions CI/CD, GHCR publishing, and automated releases ([774f84c](https://github.com/metril/3d-model-manager/commit/774f84cdf3cff0df21dad503ce3fb5736e9db001))
* optimistic mutations for favorite/rename/archive/bulk library edits ([8b4b4fe](https://github.com/metril/3d-model-manager/commit/8b4b4fe3ca5c87ead1cedb6b6b498bee6328b5e9))
* R10 studio — camera presets, X-ray, G-code layer preview, slicer deep links ([8d94423](https://github.com/metril/3d-model-manager/commit/8d944234c9a9109c0c697de6b0953b9d85526bcd))
* R11 — streaming ZIP export, dashboard, print cost, colored tags, collages, upload dedup ([aa032f1](https://github.com/metril/3d-model-manager/commit/aa032f1093531fb6d15ab7d5a57d4a61d46f511b))
* R9 library smoothness — virtualized grid, optimistic edits, hotkeys, range select ([b9f0063](https://github.com/metril/3d-model-manager/commit/b9f00635663730479ea80ade28a51fb8e93e8e4f))
* signed download-token auth + slicer-link endpoint ([3ba3a22](https://github.com/metril/3d-model-manager/commit/3ba3a222d40d555a73d0647a09011a45bc343ae3))
* upload duplicate detection (409 + name suggestion, upload-anyway retry) ([314c7e2](https://github.com/metril/3d-model-manager/commit/314c7e29bd404c3c6da7f6d8971b6771b59eb307))
* **viewer:** add a Fullscreen toolbar button reusing the R9 Shift+F toggle ([8bd32ab](https://github.com/metril/3d-model-manager/commit/8bd32abd409b6811bee29ad13490805aa7c5e9c4))
* **viewer:** add camera preset segmented control (iso/top/front/side) ([c219594](https://github.com/metril/3d-model-manager/commit/c219594ba9a1aac34b4c5fd16221ed1ca55a57e1))
* **viewer:** add X-ray shading, replacing the wireframe boolean with a shading enum ([51b6377](https://github.com/metril/3d-model-manager/commit/51b6377368faee2bde43d3cc919d462836b339a4))
* **web:** add Download ZIP buttons for a model and a followed collection ([e77372f](https://github.com/metril/3d-model-manager/commit/e77372fc40c147c12e364e7f30b2499f47371f78))
* **web:** lazy g-code layer preview + slicer metadata in PlatePanel (R10-B) ([6ab1ca0](https://github.com/metril/3d-model-manager/commit/6ab1ca0b48eef3d454198b16a429f3f53adbbc5d))
* **web:** scan progress chip in the sidebar (R9-D item 7) ([3d42888](https://github.com/metril/3d-model-manager/commit/3d42888ecd69a19c05d61249d4b0351fdb588949))
* **web:** viewer load crossfade over a thumbnail layer (R9-D item 8) ([aa48d6e](https://github.com/metril/3d-model-manager/commit/aa48d6ed5603a2c5608d2af6763590c9f2ff29b5))
* wire keyboard shortcuts across library, model detail, viewer, and shell ([f2b2804](https://github.com/metril/3d-model-manager/commit/f2b2804f616bd2c0420d3644ba3d3c067f3055bf))
* **zip:** add streaming zip-export service for models and collections ([9fc91f4](https://github.com/metril/3d-model-manager/commit/9fc91f40c1ab3a91ed459c963fe1b447c80e4063))


### Bug Fixes

* alembic CLI can load the graph; MinIO image moved to quay.io ([c3aeb02](https://github.com/metril/3d-model-manager/commit/c3aeb02974542ea692a5038f3468bcaac6ac1968))
* **alembic:** call op.f() inside upgrade/downgrade so the CLI can load the revision graph ([d73e234](https://github.com/metril/3d-model-manager/commit/d73e234883117d94f68dd8e4b8a4bcd6ac486a6c))
* close the zip export generator on client disconnect ([e94301a](https://github.com/metril/3d-model-manager/commit/e94301ac41c6782018838f1a9cfb98f08ed3a667))
* derive virtualized row height from measured container width ([8c30c1f](https://github.com/metril/3d-model-manager/commit/8c30c1f3d349cbbde332bb330110e0c24acd5608))
* fetch a model's current-revision files once per zip export, not twice ([199d482](https://github.com/metril/3d-model-manager/commit/199d4822cf61c2281c7797afb8c7518d657347a6))
* filename + real content-type in signed slicer-link download URLs ([4988f85](https://github.com/metril/3d-model-manager/commit/4988f853ac744fc441513a9fcb6b6ae8aee96b97))
* make the stats cache test-order independent ([09108b5](https://github.com/metril/3d-model-manager/commit/09108b5c8a0afd66c4ed28c8a4dabb71de8b613f))
* memoize LibraryPage items and extract slicer constants for fast refresh ([694dd65](https://github.com/metril/3d-model-manager/commit/694dd651a4c147f50c99fa6f489671d4e674d69d))
* R11 test fixtures pass tsc -b; ruff format ([b627b0a](https://github.com/metril/3d-model-manager/commit/b627b0a1353ac21419966c3d30412f261169153b))
* raise instead of segfaulting when no headless GL backend exists ([896feea](https://github.com/metril/3d-model-manager/commit/896feeafaa31ad9117f0c8b6905c175d5d70e673))
* read a zip member's gcode head/tail in one forward pass ([c92205b](https://github.com/metril/3d-model-manager/commit/c92205b8dfe3e0e9bb439e22a1d5c5d676d469f1))
* register PATCH /tags/{tag_id} in the auth sweep, allow_duplicate in bulk-delete test fixtures ([9092047](https://github.com/metril/3d-model-manager/commit/9092047af20133fa462098a2f7823dcfb82d3adf))
* sanitize names used in Content-Disposition headers and zip paths ([84a9557](https://github.com/metril/3d-model-manager/commit/84a95572b512a95ed5a44b8a415624268fe7e175))
* stop trusting X-Forwarded-* headers for signed slicer-link URLs ([b106cb4](https://github.com/metril/3d-model-manager/commit/b106cb4aad3a7294da12a4aca3fa64e373be5d05))
* stream ?member=gcode instead of buffering the whole zip member ([1d1c4b8](https://github.com/metril/3d-model-manager/commit/1d1c4b81a1b9d502659ef249b3259ab7c9aa1351))
* **web:** clear viewer thumbnail cover on error/timeout, scope Shift+F to active stage ([aedf383](https://github.com/metril/3d-model-manager/commit/aedf3839d00515af13f479887d35cd54e41c2c63))
* **web:** fix library grid ResizeObserver, column mismatch, and Escape-in-dialog ([5d7b993](https://github.com/metril/3d-model-manager/commit/5d7b99326be17c6f09c634b6ded149e1f0f29aeb))
* **web:** make test files pass tsc -b; wrap long params line ([d31e58f](https://github.com/metril/3d-model-manager/commit/d31e58ff9f742990fcb2deddee1cfa3c07134aa0))
* **web:** memoize LibraryPage items so the oxlint baseline holds ([6d86f8d](https://github.com/metril/3d-model-manager/commit/6d86f8db06013075e6e4ca8df4c74ef25f8aaeb5))
* wrap long params dict; fall back safe path segment for '.'/'..' results ([897729f](https://github.com/metril/3d-model-manager/commit/897729f442cef6573d31133c195e258c01d6f36d))


### Performance Improvements

* lazy-load gallery thumbnails, defer hover render until intent ([47f65dc](https://github.com/metril/3d-model-manager/commit/47f65dc2eb27aed52bbdb6d6e9c605f5316534d4))
* prefetch model detail on card hover/focus intent ([4733dd9](https://github.com/metril/3d-model-manager/commit/4733dd98c7168f21d711d870b261952d69777c92))
* virtualize the library grid by row with @tanstack/react-virtual ([32d5deb](https://github.com/metril/3d-model-manager/commit/32d5deb3077b1f8d3bc0618ce45727cccfb90467))
