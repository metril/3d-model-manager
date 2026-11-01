import { createContext, useContext } from "react";

import type { ExplorerSelection } from "@/components/model-detail/explorer/selection";
import type { ModelDetail } from "@/api/types";

/** What action slots (PR 4: rename, move, new folder, upload-here, bulk delete)
 * need from the explorer without prop-drilling. */
export interface ExplorerContextValue {
  model: ModelDetail;
  canUpload: boolean;
  /** Folder currently shown ("" = revision root). */
  currentFolder: string;
  setCurrentFolder: (path: string) => void;
  selection: ExplorerSelection;
}

export const ExplorerContext = createContext<ExplorerContextValue | null>(null);

export function useExplorer(): ExplorerContextValue {
  const ctx = useContext(ExplorerContext);
  if (!ctx) throw new Error("useExplorer must be used inside <FileExplorer>");
  return ctx;
}
