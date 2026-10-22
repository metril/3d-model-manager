import { PageContainer } from "@/components/ui/page-container";

/**
 * `/` print-first home (P3 Task 5 shell). `redirectForIndex` in
 * `routes.tsx` already keeps anyone without a visible printer off this
 * route (bounced to `/library`), so by the time this renders there's at
 * least one printer or an admin who could add one. Task 8 fills this in
 * with the real printers-overview dashboard (`usePrintersOverview`); this
 * is deliberately a thin placeholder until then.
 */
export function PrintDashboardPage() {
  return (
    <PageContainer width="default">
      <div>
        <h1 className="text-lg font-semibold text-foreground">Print</h1>
        <p className="text-sm text-muted-foreground">
          A live overview of your printers lands here in a later phase.
        </p>
      </div>
    </PageContainer>
  );
}
