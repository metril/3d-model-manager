import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FileExplorer } from "@/components/model-detail/explorer/FileExplorer";
import type { FileOut, ModelDetail } from "@/api/types";

/** Right-column card hosting the file explorer. `onViewIn3D` hands a file
 * off to the studio surface above (docs are never studio-viewable). */
export function FilesDocsCard({
  model,
  onViewIn3D,
}: {
  model: ModelDetail;
  onViewIn3D?: (file: FileOut) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Files</CardTitle>
      </CardHeader>
      <CardContent>
        <FileExplorer model={model} onViewIn3D={onViewIn3D} />
      </CardContent>
    </Card>
  );
}
