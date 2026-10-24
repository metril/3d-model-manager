import { CheckIcon, CopyIcon, DownloadIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

/** One-time backup code list with Copy / Download .txt. */
export function BackupCodes({ codes }: { codes: string[] }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const text = codes.join("\n");

  function copy() {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  function download() {
    const url = URL.createObjectURL(new Blob([`${text}\n`], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "backup-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">{t("twofa.backup.once")}</p>
      <ul className="grid grid-cols-2 gap-1 rounded-md border bg-muted/40 p-3 font-mono text-sm" aria-label={t("twofa.backup.list")}>
        {codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <div className="flex gap-2">
        <Button type="button" size="sm" variant="outline" onClick={copy}>
          {copied ? <CheckIcon /> : <CopyIcon />}
          {t("twofa.backup.copy")}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={download}>
          <DownloadIcon />
          {t("twofa.backup.download")}
        </Button>
      </div>
    </div>
  );
}
