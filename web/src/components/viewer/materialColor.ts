/**
 * Applies (or clears) a user colour override on a GltfPart-owned material.
 *
 * GLBs from multi-filament Bambu/Orca 3MFs carry per-vertex colours
 * (`COLOR_0`, which GLTFLoader turns into `material.vertexColors = true`).
 * three multiplies `material.color` by the vertex colour, so a swatch pick
 * would only tint those parts. A swatch therefore turns vertex colours OFF
 * (uniform colour); clearing the override ("Reset colors") restores the
 * source material's colour AND its vertex colours, i.e. the original
 * filament colours. Structural types only, so it is trivially testable.
 */
interface ColorLike {
  set(value: string): unknown;
  copy(other: ColorLike): unknown;
}
export interface OverridableMaterial {
  color?: ColorLike;
  vertexColors?: boolean;
  needsUpdate: boolean;
}

export function applyColorOverride(
  material: OverridableMaterial,
  source: Pick<OverridableMaterial, "color" | "vertexColors"> | undefined,
  color: string | undefined,
): void {
  if (material.color) {
    if (color) material.color.set(color);
    else if (source?.color) material.color.copy(source.color);
  }
  const vertexColors = color ? false : (source?.vertexColors ?? material.vertexColors ?? false);
  if (Boolean(material.vertexColors) !== vertexColors) {
    material.vertexColors = vertexColors;
    material.needsUpdate = true; // vertexColors is a shader define
  }
}
