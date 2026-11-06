/** Per-event message template editor with debounced live preview. */
import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import {
  useCreateTemplate,
  useDeleteTemplate,
  useNotificationCatalog,
  usePreviewTemplate,
  useTemplateDefaults,
  useTemplates,
  useUpdateTemplate,
  type TemplatePreview,
} from "@/api/notifications";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

const SELECT_CLASS = "h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs";
const DEBOUNCE_MS = 400;

export function TemplateEditor() {
  const { t } = useTranslation();
  const catalog = useNotificationCatalog();
  const templates = useTemplates();
  const defaults = useTemplateDefaults();
  const create = useCreateTemplate();
  const update = useUpdateTemplate();
  const del = useDeleteTemplate();
  const preview = usePreviewTemplate();
  const previewMutate = preview.mutate;

  const events = catalog.data?.events ?? [];
  const [event, setEvent] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [result, setResult] = useState<TemplatePreview | null>(null);

  const current = event || events[0]?.id || "";
  const saved = (templates.data ?? []).find((tp) => tp.event === current);
  const def = defaults.data?.[current];

  // Load title/body when the event (or its saved/default source) changes.
  const savedTitle = saved?.title_tpl;
  const savedBody = saved?.body_tpl;
  const defTitle = def?.title_tpl;
  const defBody = def?.body_tpl;
  useEffect(() => {
    setTitle(savedTitle ?? defTitle ?? "");
    setBody(savedBody ?? defBody ?? "");
  }, [current, savedTitle, savedBody, defTitle, defBody]);

  // Debounced live preview.
  useEffect(() => {
    if (!current) return;
    const timer = setTimeout(() => {
      previewMutate({ event: current, title_tpl: title, body_tpl: body }, { onSuccess: setResult });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [current, title, body, previewMutate]);

  const error = create.error ?? update.error ?? del.error;

  function save() {
    if (saved) update.mutate({ id: saved.id, body: { title_tpl: title, body_tpl: body } });
    else create.mutate({ event: current, title_tpl: title, body_tpl: body });
  }
  function reset() {
    if (saved) del.mutate(saved.id);
    else {
      setTitle(defTitle ?? "");
      setBody(defBody ?? "");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settingsNotifTemplates.title")}</CardTitle>
        <CardDescription>{t("settingsNotifTemplates.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1">
          <Label htmlFor="nt-event">{t("settingsNotifTemplates.event")}</Label>
          <select id="nt-event" className={SELECT_CLASS} value={current} onChange={(e) => setEvent(e.target.value)}>
            {events.map((ev) => (
              <option key={ev.id} value={ev.id}>
                {ev.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="nt-title">{t("settingsNotifTemplates.titleLabel")}</Label>
          <Textarea id="nt-title" rows={2} value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="nt-body">{t("settingsNotifTemplates.body")}</Label>
          <Textarea id="nt-body" rows={5} value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
        <div className="text-xs text-muted-foreground">
          {t("settingsNotifTemplates.variables")}{" "}
          {(catalog.data?.template_vars ?? []).map((v) => (
            <code key={v} className="mr-1.5">{`{{ ${v} }}`}</code>
          ))}
        </div>
        <div className="rounded-md border bg-muted/30 p-3 text-sm" aria-label={t("settingsNotifTemplates.preview")}>
          {result?.error ? (
            <p role="alert" className="text-destructive">
              {result.error}
            </p>
          ) : result ? (
            <>
              <div className="font-medium" data-testid="preview-title">{result.title}</div>
              <div className="whitespace-pre-wrap" data-testid="preview-body">{result.body}</div>
            </>
          ) : (
            <span className="text-muted-foreground">{t("settingsNotifTemplates.previewing")}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={save} disabled={!current || create.isPending || update.isPending}>
            {t("settingsNotifTemplates.save")}
          </Button>
          <Button variant="outline" onClick={reset} disabled={del.isPending}>
            {t("settingsNotifTemplates.reset")}
          </Button>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error instanceof ApiError ? error.detail : t("settingsNotifTemplates.saveError")}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
