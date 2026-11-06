import { Trans, useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { StorageConfigIn, StorageConfigOut, StorageScheme } from "@/api/types";

type FieldKind = "text" | "password" | "number" | "checkbox" | "select";

interface SelectOption {
  value: string;
  label: string;
}

interface FieldSpec {
  name: string;
  label: string;
  kind: FieldKind;
  required?: boolean;
  placeholder?: string;
  options?: SelectOption[];
}

export function backendLabels(t: TFunction): Record<StorageScheme, string> {
  return {
    local: t("settingsStorageForm.local"),
    smb: t("settingsStorageForm.smb"),
    s3: t("settingsStorageForm.s3"),
  };
}

/** Secret fields per backend -- the ones a save/test/migrate payload must
 * drop when left blank instead of overwriting the stored value (Task 7
 * brief: "leave blank = keep"). */
export const SECRET_FIELDS: Record<StorageScheme, string[]> = {
  local: [],
  smb: ["password"],
  s3: ["secret_key"],
};

function smbFields(t: TFunction): FieldSpec[] {
  return [
    { name: "host", label: t("settingsStorageForm.host"), kind: "text", required: true, placeholder: t("settingsStorageForm.hostPlaceholder") },
    { name: "share", label: t("settingsStorageForm.share"), kind: "text", required: true },
    { name: "root", label: t("settingsStorageForm.root"), kind: "text", placeholder: t("settingsStorageForm.rootPlaceholder") },
    { name: "username", label: t("settingsStorageForm.username"), kind: "text", required: true },
    { name: "password", label: t("settingsStorageForm.password"), kind: "password", required: true },
    { name: "port", label: t("settingsStorageForm.port"), kind: "number", placeholder: "445" },
    { name: "encrypt", label: t("settingsStorageForm.encrypt"), kind: "checkbox" },
  ];
}

function s3Fields(t: TFunction): FieldSpec[] {
  return [
    { name: "bucket", label: t("settingsStorageForm.bucket"), kind: "text", required: true },
    { name: "access_key", label: t("settingsStorageForm.accessKey"), kind: "text", required: true },
    { name: "secret_key", label: t("settingsStorageForm.secretKey"), kind: "password", required: true },
    { name: "endpoint_url", label: t("settingsStorageForm.endpoint"), kind: "text", placeholder: t("settingsStorageForm.endpointPlaceholder") },
    { name: "region", label: t("settingsStorageForm.region"), kind: "text" },
    { name: "prefix", label: t("settingsStorageForm.prefix"), kind: "text", placeholder: t("settingsStorageForm.prefixPlaceholder") },
    {
      name: "addressing",
      label: t("settingsStorageForm.addressing"),
      kind: "select",
      options: [
        { value: "path", label: t("settingsStorageForm.pathStyle") },
        { value: "virtual", label: t("settingsStorageForm.virtualStyle") },
      ],
    },
  ];
}

export function fieldsByBackend(t: TFunction): Record<StorageScheme, FieldSpec[]> {
  return {
    local: [],
    smb: smbFields(t),
    s3: s3Fields(t),
  };
}

/** Required fields still blank in `draft`, skipping any secret field that's
 * allowed to stay blank because the backend already has one stored (Task 7:
 * "leave blank = keep" for a secret belonging to the currently-active
 * backend). Returns field labels for display; empty means the candidate is
 * ready to test/save/migrate. */
export function missingRequiredFields(
  draft: StorageConfigIn,
  active: StorageConfigOut | undefined,
  t: TFunction,
): string[] {
  const secretFields = SECRET_FIELDS[draft.backend];
  const activeHasSecret = (name: string) =>
    active !== undefined && active.backend === draft.backend && active.config[name] === "***";

  return fieldsByBackend(t)[draft.backend]
    .filter((field) => field.required)
    .filter((field) => {
      const raw = draft.config[field.name];
      const blank = raw === undefined || raw === "";
      if (!blank) return false;
      return !(secretFields.includes(field.name) && activeHasSecret(field.name));
    })
    .map((field) => field.label);
}

/** Drops blank secret fields from a candidate config before it's sent to
 * `/settings/storage`, `/settings/storage/test`, or `/settings/storage/migrate`
 * -- a blank secret field means "keep whatever is already stored", not
 * "set it to empty". */
export function stripBlankSecrets(draft: StorageConfigIn): StorageConfigIn {
  const secretFields = SECRET_FIELDS[draft.backend];
  const config: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(draft.config)) {
    if (secretFields.includes(key) && (value === undefined || value === "")) continue;
    config[key] = value;
  }
  return { backend: draft.backend, config };
}

/** Replaces the redacted `"***"` GET returns for a set secret with `""` so
 * the form never puts the sentinel into an input's value (Task 7: "secrets
 * are never displayed"). Pairs with `stripBlankSecrets` above: a field left
 * at `""` here is what "leave unchanged" looks like. */
export function seedDraft(out: StorageConfigOut): StorageConfigIn {
  const config = { ...out.config };
  for (const field of SECRET_FIELDS[out.backend]) {
    if (config[field] === "***") config[field] = "";
  }
  return { backend: out.backend, config };
}

interface StorageBackendFormProps {
  value: StorageConfigIn;
  onChange: (next: StorageConfigIn) => void;
  disabled?: boolean;
  idPrefix?: string;
}

/** Backend picker + per-backend connection fields (Task 7 brief). Fully
 * controlled: the caller (`SettingsPage`) owns the draft config so it can
 * feed the same value into Test/Save/Migrate. */
export function StorageBackendForm({ value, onChange, disabled, idPrefix = "storage" }: StorageBackendFormProps) {
  const { t } = useTranslation();
  const labels = backendLabels(t);
  const fields = fieldsByBackend(t)[value.backend];

  function setField(name: string, fieldValue: unknown) {
    onChange({ ...value, config: { ...value.config, [name]: fieldValue } });
  }

  return (
    <div className="space-y-4">
      <div className="flex max-w-xs flex-col gap-1.5">
        <Label htmlFor={`${idPrefix}-backend`}>{t("settingsStorageForm.backend")}</Label>
        <Select
          value={value.backend}
          onValueChange={(next) => onChange({ backend: next as StorageScheme, config: {} })}
          disabled={disabled}
        >
          <SelectTrigger id={`${idPrefix}-backend`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(labels) as StorageScheme[]).map((scheme) => (
              <SelectItem key={scheme} value={scheme}>
                {labels[scheme]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t("settingsStorageForm.noConfig")}
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {fields.map((field) => {
            const id = `${idPrefix}-${field.name}`;
            const raw = value.config[field.name];

            if (field.kind === "checkbox") {
              return (
                <Label key={field.name} className="flex items-center gap-2 font-normal">
                  <Checkbox
                    id={id}
                    checked={raw === undefined ? true : Boolean(raw)}
                    onCheckedChange={(checked) => setField(field.name, checked === true)}
                    disabled={disabled}
                  />
                  {field.label}
                </Label>
              );
            }

            if (field.kind === "select" && field.options) {
              const current = typeof raw === "string" ? raw : field.options[0].value;
              return (
                <div key={field.name} className="flex flex-col gap-1.5">
                  <Label htmlFor={id}>{field.label}</Label>
                  <Select value={current} onValueChange={(next) => setField(field.name, next)} disabled={disabled}>
                    <SelectTrigger id={id}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {field.options.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              );
            }

            const stringValue = typeof raw === "string" || typeof raw === "number" ? String(raw) : "";
            return (
              <div key={field.name} className="flex flex-col gap-1.5">
                <Label htmlFor={id}>
                  {field.label}
                  {field.required ? " *" : ""}
                </Label>
                <Input
                  id={id}
                  type={field.kind === "password" ? "password" : field.kind === "number" ? "number" : "text"}
                  value={stringValue}
                  placeholder={field.kind === "password" ? t("settingsStorageForm.unchanged") : field.placeholder}
                  onChange={(event) => {
                    const next = event.target.value;
                    if (field.kind === "number") {
                      setField(field.name, next === "" ? undefined : Number(next));
                    } else {
                      setField(field.name, next);
                    }
                  }}
                  disabled={disabled}
                  autoComplete={field.kind === "password" ? "new-password" : "off"}
                />
              </div>
            );
          })}
        </div>
      )}

      {value.backend === "s3" ? (
        <p className="text-xs text-muted-foreground">
          {t("settingsStorageForm.lifecycle")}
        </p>
      ) : null}
      {value.backend === "smb" ? (
        <p className="text-xs text-muted-foreground">
          <Trans i18nKey="settingsStorageForm.smbHint" components={{ code: <code /> }} />
        </p>
      ) : null}
    </div>
  );
}
