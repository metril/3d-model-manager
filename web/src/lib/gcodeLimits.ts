/** Above this, don't load the gcode text into the browser. */
export const TOO_LARGE_TO_PREVIEW_BYTES = 150 * 1024 * 1024;

/** Decoded text length (UTF-16 units) vs the size cap. */
export function isTooLarge(textLength: number): boolean {
  return textLength > TOO_LARGE_TO_PREVIEW_BYTES;
}
