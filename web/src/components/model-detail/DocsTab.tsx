/**
 * Docs tab (R13c): the same row layout as `FilesTab.tsx` but scoped to
 * `kind === "doc"` files, with a doc-specific expandable preview per row
 * instead of a mesh/sliced meta line -- pdf renders inline via `<iframe>`,
 * md/txt fetch their raw text and render it (markdown vs. plain `<pre>`),
 * docx (and anything else doc-kind) has no inline preview at all, just the
 * shared `FileActionsMenu` for Download.
 *
 * Preview is a per-row toggle (not always-on) so opening this tab doesn't
 * fire an md/txt fetch for every doc up front -- docs tend to be few per
 * model, but there's no reason to pay for previews nobody opens.
 */
import { useEffect, useState } from "react";
import { FileText as FileTextIcon } from "lucide-react";

import { FileActionsMenu } from "@/components/model-detail/FileActionsMenu";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FileRow } from "@/components/model-detail/FileRow";
import { formatDate, formatDateTime, humanizeBytes } from "@/lib/format";
import { formatIcon } from "@/lib/formatMeta";
import { MARKDOWN_CLASSNAME, renderMarkdown } from "@/lib/markdown";
import type { FileOut, ModelDetail } from "@/api/types";

function inlineDownloadUrl(file: FileOut): string {
  return `/api/files/${file.id}/download?inline=1`;
}

function DocThumb({ file }: { file: FileOut }) {
  const Icon = formatIcon(file.format);
  return (
    <div className="flex size-10 items-center justify-center rounded bg-muted text-muted-foreground">
      <Icon className="size-5" />
    </div>
  );
}

/** Fetches and renders the raw text body of an md/txt doc once its row's
 * preview is toggled open. */
function TextDocPreview({ file }: { file: FileOut }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    fetch(inlineDownloadUrl(file), { credentials: "include" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      })
      .then((body) => {
        if (!cancelled) setText(body);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [file]);

  if (loading) return <p className="text-xs text-muted-foreground">Loading…</p>;
  if (error || text === null) return <p className="text-xs text-muted-foreground">Could not load this file.</p>;
  if (file.format === "md") {
    return <div className={MARKDOWN_CLASSNAME} dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} />;
  }
  return <pre className="whitespace-pre-wrap text-xs">{text}</pre>;
}

function DocPreview({ file }: { file: FileOut }) {
  if (file.format === "pdf") {
    return <iframe src={inlineDownloadUrl(file)} title={file.rel_path} className="h-[600px] w-full rounded border" />;
  }
  if (file.format === "md" || file.format === "txt") {
    return <TextDocPreview file={file} />;
  }
  return null;
}

function isPreviewable(file: FileOut): boolean {
  return file.format === "pdf" || file.format === "md" || file.format === "txt";
}

export function DocsTab({ model }: { model: ModelDetail }) {
  const [openPreviewId, setOpenPreviewId] = useState<number | null>(null);
  const files = (model.current_revision?.files ?? []).filter((file) => file.kind === "doc");

  if (files.length === 0) {
    return <p className="py-8 text-center text-sm text-muted-foreground">No documents on the current revision yet.</p>;
  }

  return (
    <div className="space-y-4">
      <ul role="list" className="@container/files divide-y">
        {files.map((file) => {
          const previewOpen = openPreviewId === file.id;
          return (
            <FileRow
              key={file.id}
              thumb={<DocThumb file={file} />}
              path={file.rel_path}
              facts={
                <>
                  <span>{humanizeBytes(file.size)}</span>
                  <span className="@sm/files:hidden">{formatDate(file.mtime)}</span>
                  <span className="hidden @sm/files:inline">{formatDateTime(file.mtime)}</span>
                  <Badge variant={file.verified_at ? "secondary" : "outline"}>
                    {file.verified_at ? "stored" : "processing"}
                  </Badge>
                </>
              }
              actions={
                <>
                  {isPreviewable(file) ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setOpenPreviewId(previewOpen ? null : file.id)}
                    >
                      <FileTextIcon className="size-4" />
                      <span className="sr-only @sm/files:not-sr-only">{previewOpen ? "Hide preview" : "Preview"}</span>
                    </Button>
                  ) : null}
                  <FileActionsMenu file={file} model={model} isDoc />
                </>
              }
              below={
                previewOpen ? (
                  <div data-testid={`doc-preview-${file.id}`}>
                    <DocPreview file={file} />
                  </div>
                ) : null
              }
            />
          );
        })}
      </ul>
    </div>
  );
}
