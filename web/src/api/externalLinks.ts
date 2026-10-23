import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";

export interface ExternalLink {
  id: number;
  name: string;
  url: string;
  icon: string | null;
  sort_order: number;
  open_mode: "iframe" | "newtab";
}

export type ExternalLinkInput = Omit<ExternalLink, "id">;

const KEY = ["external-links"] as const;

export function useExternalLinks() {
  return useQuery({ queryKey: KEY, queryFn: () => api.get<ExternalLink[]>("/external-links") });
}

function useInvalidating<T>(fn: (arg: T) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: KEY }),
  });
}

export function useCreateExternalLink() {
  return useInvalidating((body: ExternalLinkInput) => api.post<ExternalLink>("/external-links", body));
}

export function useUpdateExternalLink() {
  return useInvalidating(({ id, ...body }: Partial<ExternalLinkInput> & { id: number }) =>
    api.patch<ExternalLink>(`/external-links/${id}`, body),
  );
}

export function useDeleteExternalLink() {
  return useInvalidating((id: number) => api.delete<void>(`/external-links/${id}`));
}
