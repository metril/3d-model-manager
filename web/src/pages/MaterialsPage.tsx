import { MaterialsSection } from "@/components/settings/MaterialsSection";
import { PageContainer } from "@/components/ui/page-container";

/** `/inventory/materials` (P3 Task 5): the filament-profiles CRUD that used
 * to live under Settings' Materials tab, now its own page. No page-level
 * heading here -- `MaterialsSection`'s own card already carries a
 * "Materials" `CardTitle` + description, so a page `<h1>` on top of it was
 * a duplicate heading (fix-review finding 10). */
export function MaterialsPage() {
  return (
    <PageContainer width="default">
      <MaterialsSection />
    </PageContainer>
  );
}
