import { useAuth } from "@/api/auth";
import { PrinterCard, type PrinterCardProps } from "@/components/print/PrinterCard";
import { usePrinterCardExtras } from "@/components/print/usePrinterCardExtras";

/** `PrinterCard` with the shared extras wired in; every other prop passes through. */
export function ManagedPrinterCard(props: PrinterCardProps) {
  const { canPrinter, can } = useAuth();
  const { printer, status } = props.item;
  const extras = usePrinterCardExtras(printer, status, {
    canPrint: canPrinter(printer.id, "print"),
    canAdmin: can("settings:write"),
  });
  const { modals, ...slots } = extras;
  return (
    <>
      <PrinterCard {...slots} {...props} />
      {modals}
    </>
  );
}
