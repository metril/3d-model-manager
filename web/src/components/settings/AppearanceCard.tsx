import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ACCENTS, STYLES, useAppearance } from "@/lib/appearance";
import { cn } from "@/lib/utils";

const MODES = ["light", "dark", "system"] as const;

// Swatches mirror the hue/chroma pairs in index.css (`:root[data-accent=...]`).
const SWATCH: Record<(typeof ACCENTS)[number], string> = {
  orange: "oklch(0.646 0.176 41.1)",
  green: "oklch(0.646 0.16 149)",
  teal: "oklch(0.646 0.12 190)",
  blue: "oklch(0.646 0.19 255)",
  purple: "oklch(0.646 0.2 300)",
  red: "oklch(0.646 0.21 25)",
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="text-sm font-medium">{label}</div>
      <div role="group" aria-label={label} className="flex flex-wrap gap-2">
        {children}
      </div>
    </div>
  );
}

/** Settings -> Appearance: mode x style x accent. Per-browser, stored in
 * localStorage (mode by next-themes, style/accent by `lib/appearance`). */
export function AppearanceCard() {
  const { theme, setTheme } = useTheme();
  const { style, accent, setStyle, setAccent } = useAppearance();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Appearance</CardTitle>
        <CardDescription>Applies to this browser only.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Row label="Mode">
          {MODES.map((mode) => (
            <Button
              key={mode}
              type="button"
              size="sm"
              variant={(theme ?? "system") === mode ? "default" : "outline"}
              aria-pressed={(theme ?? "system") === mode}
              onClick={() => setTheme(mode)}
              className="capitalize"
            >
              {mode}
            </Button>
          ))}
        </Row>
        <Row label="Style">
          {STYLES.map((value) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant={style === value ? "default" : "outline"}
              aria-pressed={style === value}
              onClick={() => setStyle(value)}
              className="capitalize"
            >
              {value}
            </Button>
          ))}
        </Row>
        <Row label="Accent">
          {ACCENTS.map((value) => (
            <button
              key={value}
              type="button"
              aria-label={value}
              aria-pressed={accent === value}
              title={value}
              onClick={() => setAccent(value)}
              style={{ backgroundColor: SWATCH[value] }}
              className={cn(
                "size-7 rounded-full border-2 border-transparent outline-offset-2 focus-visible:outline-2",
                accent === value && "border-foreground",
              )}
            />
          ))}
        </Row>
      </CardContent>
    </Card>
  );
}
