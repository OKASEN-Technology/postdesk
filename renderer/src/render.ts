/**
 * Spec in, JPEG out: satori lays the slide out as SVG, resvg rasterises it,
 * jpeg-js encodes it (Instagram takes JPEG only). The spec is validated
 * against `schema/slide.json` first, so a bad field is a clear 400 rather
 * than a blank slide.
 */

import { Resvg } from "@resvg/resvg-js";
import { encode } from "jpeg-js";
import satori from "satori";

import { layouts, sizeOf, type Slide } from "./slides.js";
import { loadTheme, type Theme } from "./theme.js";
import { validateSpec } from "./validate.js";

export const JPEG_QUALITY = 92;

export interface RenderOptions {
  /** Overrides the spec's `theme`; both default to "default". */
  theme?: string;
  quality?: number;
  /** Return the SVG instead of a JPEG (debugging a layout). */
  svg?: boolean;
}

export class RenderError extends Error {
  constructor(message: string, public status = 400) {
    super(message);
  }
}

function fontsFor(theme: Theme) {
  return theme.loadedFonts.map((f) => ({ name: f.name, data: f.data, weight: f.weight, style: f.style }));
}

/** The slide as SVG (satori). */
export async function renderSvg(spec: Slide, theme: Theme): Promise<string> {
  const node = layouts(theme).render(spec);
  const size = sizeOf(spec);
  // satori is typed for React nodes; our `h` builds the same shape.
  return satori(node as never, { ...size, fonts: fontsFor(theme) });
}

/** Validate, lay out, rasterise, encode. */
export async function render(spec: unknown, opts: RenderOptions = {}): Promise<Buffer> {
  const problems = validateSpec(spec);
  if (problems.length) throw new RenderError(`invalid slide spec: ${problems.join("; ")}`);
  const slide = spec as Slide;
  const themeName = opts.theme ?? slide.theme ?? "default";
  const theme = loadTheme(themeName);
  const svg = await renderSvg(slide, theme);
  if (opts.svg) return Buffer.from(svg, "utf8");
  const size = sizeOf(slide);
  const png = new Resvg(svg, { fitTo: { mode: "width", value: size.width } }).render();
  const rgba = png.pixels;
  const jpeg = encode({ data: rgba, width: png.width, height: png.height }, opts.quality ?? JPEG_QUALITY);
  return Buffer.from(jpeg.data);
}
