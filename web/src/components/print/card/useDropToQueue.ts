import { useCallback } from "react";
import { toast } from "sonner";

import { useCreateModel } from "@/api/library";
import { useEnqueue } from "@/api/queue";
import { uploadFile } from "@/api/upload";
import { resolveDroppedFiles } from "@/lib/droppedFiles";

const PRINTABLE = /\.(3mf|gcode|gcode\.3mf)$/i;

/** Drop a sliced file on a printer card: new library model -> upload -> queue entry targeting that printer. */
export function useDropToQueue(printerId: number) {
  const createModel = useCreateModel();
  const enqueue = useEnqueue();
  return useCallback(
    async (dataTransfer: DataTransfer) => {
      const files = (await resolveDroppedFiles(dataTransfer)).filter((f) => PRINTABLE.test(f.file.name));
      if (files.length === 0) {
        toast.error("Drop a .3mf or .gcode file to queue it.");
        return;
      }
      for (const { file, relPath } of files) {
        try {
          const model = await createModel.mutateAsync({ name: file.name.replace(/\.(gcode\.3mf|3mf|gcode)$/i, "") });
          if (!model.current_revision) throw new Error("no revision");
          await uploadFile({ modelId: model.id, revisionId: model.current_revision.id, relPath, file });
          await enqueue.mutateAsync({ model_id: model.id, printer_id: printerId });
          toast.success(`Queued ${file.name}`);
        } catch (e) {
          toast.error(`Could not queue ${file.name}${e instanceof Error ? `: ${e.message}` : ""}`);
        }
      }
    },
    [createModel, enqueue, printerId],
  );
}
