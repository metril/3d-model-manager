# Q12 live verification (A1 Mini)

1. Dashboard `/` at M and L size: each card shows the maintenance badge and detection badge (print permission), plug row, Camera split button (Open in window / camera wall), Firmware, Temperature history.
2. Card menu: Info, Diagnose (admin) and the HMS count chip open their modals from the dashboard, same as `/printers/$id`.
3. S size: compact card shows state + badges only; expanding it shows the full card.
4. AMS (if attached): the loaded-tray dot sits on the slot the printer reports; with the external spool active the dot is on External; after unload, no dot.
5. Door chip: absent on the A1 Mini (no door sensor). On an X1/P1/H2 model it reads Door open/closed and flips when the door is opened.
6. Start a print from a stored 3MF: the status thumbnail shows the plate image (blob plate thumb); a job without a stored 3MF shows the placeholder.
7. Switch locale-sensitive strings: no untranslated key names (`card.*`, `dash.*`) appear anywhere on the card, summary bar, bulk toolbar or dev-mode banner.
