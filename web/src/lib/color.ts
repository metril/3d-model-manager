/**
 * Small client-side color-distance helper (P5 Task 10): `SlotAssignPopover`
 * sorts its spool combobox so colors closest to the tray's own read color
 * come first. OKLab is a perceptually-uniform space, so plain Euclidean
 * distance in it ("deltaE" here, a loose use of the term) tracks "looks
 * similar" far better than comparing raw sRGB bytes would.
 */

/** `#RRGGBB` -> linear-light sRGB components in `[0, 1]`. */
function hexToLinearSrgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  const toLinear = (c: number) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return [toLinear((n >> 16) & 0xff), toLinear((n >> 8) & 0xff), toLinear(n & 0xff)];
}

/** Linear sRGB -> OKLab (Björn Ottosson's reference matrices). */
function linearSrgbToOklab(r: number, g: number, b: number): [number, number, number] {
  const l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b;
  const m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b;
  const s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b;

  const l_ = Math.cbrt(l);
  const m_ = Math.cbrt(m);
  const s_ = Math.cbrt(s);

  return [
    0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_,
    1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_,
    0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_,
  ];
}

/** `#RRGGBB` -> OKLab, or `null` for an unparseable hex. */
export function hexToOklab(hex: string): [number, number, number] | null {
  if (!/^#[0-9A-Fa-f]{6}$/.test(hex)) return null;
  const [r, g, b] = hexToLinearSrgb(hex);
  return linearSrgbToOklab(r, g, b);
}

/** Perceptual color distance between two `#RRGGBB` hex colors (Euclidean
 * distance in OKLab). Returns `Infinity` when either color is unparseable,
 * so callers that sort by this naturally push unknown colors to the end. */
export function deltaE(a: string, b: string): number {
  const oa = hexToOklab(a);
  const ob = hexToOklab(b);
  if (!oa || !ob) return Infinity;
  const [l1, a1, b1] = oa;
  const [l2, a2, b2] = ob;
  return Math.sqrt((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2);
}
