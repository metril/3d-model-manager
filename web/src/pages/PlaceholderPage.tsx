import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Honest "not built yet" stub (P3 Task 5), replacing `ComingSoonPage`.
 * Unlike that page's generic "Coming in M2+", this names the phase that
 * will actually ship it (`phase`, e.g. "P5") and a one-line `blurb` on what
 * it will do, so a nav link that resolves here doesn't read as a dead end.
 */
export function PlaceholderPage({
  title,
  phase,
  blurb,
}: {
  title: string;
  phase: string;
  blurb: string;
}) {
  return (
    <Card className="mx-auto mt-12 max-w-md">
      <CardHeader className="text-center">
        <CardTitle>{title}</CardTitle>
        <CardDescription>
          Coming in a later phase ({phase}). {blurb}
        </CardDescription>
      </CardHeader>
    </Card>
  );
}
