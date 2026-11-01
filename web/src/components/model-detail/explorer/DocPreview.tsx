/**
 * Doc preview body for the explorer's side pane: pdf renders inline via
 * `<iframe>`, md/txt fetch their raw text (markdown vs. plain `<pre>`),
 * anything else has no inline preview (just the shared `FileActionsMenu`
 * Download). Only mounted once a row's Preview toggle is opened, so no
 * md/txt fetch fires for docs nobody previews.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { MARKDOWN_CLASSNAME, renderMarkdown } from "@/lib/markdown";
import type { FileOut } from "@/api/types";

function inlineDownloadUrl(file: FileOut): string {
  return `/api/files/${file.id}/download?inline=1`;
}

function TextDocPreview({ file }: { file: FileOut }) {
  const { t } = useTranslation();
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

  if (loading) return <p className="text-xs text-muted-foreground">{t("explorer.previewLoading")}</p>;
  if (error || text === null) return <p className="text-xs text-muted-foreground">{t("explorer.previewError")}</p>;
  if (file.format === "md") {
    return <div className={MARKDOWN_CLASSNAME} dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} />;
  }
  return <pre className="whitespace-pre-wrap text-xs">{text}</pre>;
}

export function DocPreview({ file }: { file: FileOut }) {
  if (file.format === "pdf") {
    return <iframe src={inlineDownloadUrl(file)} title={file.rel_path} className="h-[60vh] min-h-72 w-full rounded border" />;
  }
  if (file.format === "md" || file.format === "txt") {
    return <TextDocPreview file={file} />;
  }
  return null;
}
