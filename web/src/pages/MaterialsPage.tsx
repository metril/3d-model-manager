import { MaterialsSection } from "@/components/settings/MaterialsSection";
import { PageContainer } from "@/components/ui/page-container";

/** `/inventory/materials` (P3 Task 5): the filament-profiles CRUD that used
 * to live under Settings' Materials tab, now its own page. */
export function MaterialsPage() {
  return (
    <PageContainer width="default">
      <div>
        <h1 className="text-lg font-semibold text-foreground">Materials</h1>
        <p className="text-sm text-muted-foreground">Filament profiles a print can reference.</p>
      </div>
      <MaterialsSection />
    </PageContainer>
  );
}
