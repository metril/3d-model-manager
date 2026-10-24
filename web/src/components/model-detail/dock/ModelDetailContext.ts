import { createContext, useContext } from "react";

import type { ModelDetail } from "@/api/types";
import type { useStudioSelection } from "@/components/model-detail/studioSelection";

export type ModelDetailContextValue = {
  model: ModelDetail;
  editMode: boolean;
  setEditMode: (editMode: boolean) => void;
  relocateOpen: boolean;
  setRelocateOpen: (open: boolean) => void;
  studio: ReturnType<typeof useStudioSelection>;
};

const ModelDetailContext = createContext<ModelDetailContextValue | null>(null);

export const ModelDetailProvider = ModelDetailContext.Provider;

export function useModelDetail(): ModelDetailContextValue {
  const value = useContext(ModelDetailContext);
  if (!value) throw new Error("useModelDetail must be used inside <ModelDetailProvider>");
  return value;
}

export { usePortalContainer } from "@/lib/portalContainer";
