# Q8c live verify (A1 Mini + real data)

1. Open `/stats`: all 8 widgets render with real history; switch 7/30/90/365 days, numbers change.
2. Drag a widget by its grip (mouse, and keyboard: focus grip, Space, arrows, Space); reload; order persists.
   Log in as another user: layout is independent.
3. Resize button cycles small/medium/large; mobile width stacks to one column.
4. Dark mode + each accent: chart colors legible, heatmap cells visible, hover tooltip on the filament chart.
5. Printer detail page: "Temperature history" shows nozzle/bed lines after >=10 min of sampling
   (printerd 60 s timer); changing range refetches; chamber line absent on A1 Mini.
6. "AMS history" shows humidity and temperature for unit 1 (needs an AMS lite connected).
7. Settings -> General -> Appearance -> Language lists English; reload keeps it; sidebar, topbar
   and Settings tabs render. Clear `tdmm.lang` to confirm browser-language fallback to English.
8. `cd web && npm run i18n:check` passes; temporarily add `t("nope.key")` and confirm it fails.
