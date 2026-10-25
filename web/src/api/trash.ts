/** Library trash (backend/app/api/trash.py): list / restore / purge trashed models. */
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { TrashCount, TrashList } from "@/api/types";

export const trashQueryOptions = queryOptions({
  queryKey: ["trash"] as const,
  queryFn: () => api.get<TrashList>("/trash"),
});

export function useTrash() {
  return useQuery(trashQueryOptions);
}

/** The server commits per batch but a failed request may still have changed
 * state, so every mutation reconciles in `onSettled`, not `onSuccess`. */
function useTrashMutation(path: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ids: number[]) => api.post<TrashCount>(path, { ids }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: trashQueryOptions.queryKey });
      void queryClient.invalidateQueries({ queryKey: ["models"] });
      void queryClient.invalidateQueries({ queryKey: ["storage"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}

export const useRestoreTrash = () => useTrashMutation("/trash/restore");
export const usePurgeTrash = () => useTrashMutation("/trash/purge");
