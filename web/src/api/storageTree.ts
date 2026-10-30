/**
 * Query hook for the folder-browser view (R13b): `GET /storage/tree?path=`
 * lists the immediate subdirectories and directly-contained models under a
 * raw storage path, one level at a time -- navigation happens by re-querying
 * with a new `path`, not by fetching a whole tree up front.
 */
import { useInfiniteQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { StorageTreeOut } from "@/api/types";

/** Files fetched per page (the server's default page size is 500). */
export const TREE_PAGE_SIZE = 500;

function buildTreeUrl(path: string, offset: number): string {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  if (offset > 0) {
    params.set("limit", String(TREE_PAGE_SIZE));
    params.set("offset", String(offset));
  }
  const query = params.toString();
  return query ? `/storage/tree?${query}` : "/storage/tree";
}

/** First page plus any "load more" pages merged into one `StorageTreeOut`
 * (dirs/model come from page one; files are concatenated). */
export function useStorageTree(path: string) {
  const query = useInfiniteQuery({
    queryKey: ["storage", "tree", path] as const,
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api.get<StorageTreeOut>(buildTreeUrl(path, pageParam)),
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, page) => n + page.files.length, 0);
      return loaded < (last.total_files ?? 0) ? loaded : undefined;
    },
    select: (data): StorageTreeOut => ({
      ...data.pages[0],
      files: data.pages.flatMap((page) => page.files),
    }),
  });
  return {
    data: query.data,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
    hasMore: query.hasNextPage,
    loadingMore: query.isFetchingNextPage,
    loadMore: () => void query.fetchNextPage(),
  };
}
