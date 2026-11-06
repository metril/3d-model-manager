import { useTranslation } from "react-i18next";
import { useState } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";

/** `compact` hides the hash text until the enclosing `@container/files` is
 * `@md` wide (icon only below that); the aria-label always carries the hash. */
export function CopyableHash({ hash, compact = false }: { hash: string; compact?: boolean }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    void navigator.clipboard.writeText(hash).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={hash}
      aria-label={t("mdHash.copy", { hash })}
      className="inline-flex items-center gap-1 rounded px-1 font-mono text-xs hover:bg-muted"
    >
      <span className={compact ? "hidden @md/files:inline" : undefined}>{hash.slice(0, 10)}</span>
      {copied ? <CheckIcon className="size-3 text-success" /> : <CopyIcon className="size-3 text-muted-foreground" />}
    </button>
  );
}
