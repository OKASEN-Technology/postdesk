/**
 * A theme is a folder with a `theme.json` (docs/themes.md): the palette,
 * the fonts (files beside it), a wordmark (an SVG mark drawn in
 * `currentColor`, plus the text set next to it), the handle and site the
 * call-to-action slide shows, and the few fixed strings a slide prints.
 *
 * Themes are looked up in `RENDERER_THEMES` (a `:`-separated list of
 * folders) and then in the package's own `themes/`, so a user adds a theme
 * by mounting a folder, never by editing this package.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export interface Palette {
  /** The light ground (editorial slides). */
  paper: string;
  /** The dark ground (data slides, scrims). */
  deep: string;
  /** A raised panel on the dark ground. */
  deepRaised: string;
  /** Hairlines on the dark ground. */
  rule: string;
  /** The accent on the light ground. */
  accent: string;
  /** The accent on the dark ground. */
  accentLight: string;
  /** A warm highlight: tags, "new" markers, a Story's figure tag. */
  highlight: string;
  /** Body text on the light ground. */
  ink: string;
  /** Secondary text on the light ground. */
  sub: string;
  /** A neutral tag ground. */
  stone: string;
  /** Secondary text on the dark ground. */
  muted: string;
  /** Lighter secondary text on the dark ground. */
  mutedLight: string;
  /** A rise, in a table. */
  up: string;
  /** A fall, in a table. */
  down: string;
}

export interface FontFile {
  family: string;
  weight: 400 | 500 | 600 | 700;
  style: "normal" | "italic";
  file: string;
}

export interface ThemeFile {
  name: string;
  description?: string;
  palette: Palette;
  /** Which family plays which role. */
  roles: { serif: string; sans: string; mono: string };
  fonts: FontFile[];
  wordmark: { mark: string; text: string; markWidth?: number; markHeight?: number };
  /** Shown on the call-to-action slide. */
  handle: string;
  site: string;
  ctaLine: string;
  strings?: Partial<Strings>;
}

export interface Strings {
  swipe: string;
  linkInBio: string;
  newEntry: string;
  band: string;
  level: string;
}

export const DEFAULT_STRINGS: Strings = {
  swipe: "Swipe",
  linkInBio: "Link in bio",
  newEntry: "NEW",
  band: "range",
  level: "now",
};

export interface LoadedFont {
  name: string;
  data: ArrayBuffer;
  weight: 400 | 500 | 600 | 700;
  style: "normal" | "italic";
}

export interface Theme extends ThemeFile {
  dir: string;
  strings: Strings;
  loadedFonts: LoadedFont[];
  /** The mark's SVG source, `currentColor` still in it. */
  markSvg: string;
}

const HERE = dirname(fileURLToPath(import.meta.url));
export const PACKAGE_THEMES = resolve(HERE, "..", "themes");

export function themeDirs(): string[] {
  const extra = (process.env.RENDERER_THEMES ?? "").split(":").filter(Boolean).map((p) => resolve(p));
  return [...extra, PACKAGE_THEMES];
}

function isDir(p: string): boolean {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

/** Every theme name available, first folder wins on a clash. */
export function listThemes(): string[] {
  const names: string[] = [];
  for (const dir of themeDirs()) {
    if (!isDir(dir)) continue;
    for (const name of readdirSync(dir)) {
      if (!names.includes(name) && isDir(join(dir, name)) && isDir(dir) && exists(join(dir, name, "theme.json"))) {
        names.push(name);
      }
    }
  }
  return names.sort();
}

function exists(p: string): boolean {
  try {
    statSync(p);
    return true;
  } catch {
    return false;
  }
}

const REQUIRED_PALETTE: (keyof Palette)[] = [
  "paper", "deep", "deepRaised", "rule", "accent", "accentLight", "highlight", "ink", "sub", "stone", "muted",
  "mutedLight", "up", "down",
];

export class ThemeError extends Error {}

function validate(file: ThemeFile, dir: string): void {
  const missing = REQUIRED_PALETTE.filter((k) => !file.palette?.[k]);
  if (missing.length) throw new ThemeError(`${dir}/theme.json: palette is missing ${missing.join(", ")}`);
  for (const role of ["serif", "sans", "mono"] as const) {
    const family = file.roles?.[role];
    if (!family) throw new ThemeError(`${dir}/theme.json: roles.${role} is missing`);
    if (!file.fonts?.some((f) => f.family === family)) {
      throw new ThemeError(`${dir}/theme.json: no font file for the ${role} family ${JSON.stringify(family)}`);
    }
  }
  if (!file.wordmark?.mark || typeof file.wordmark.text !== "string") {
    throw new ThemeError(`${dir}/theme.json: wordmark needs a mark (an SVG file) and a text`);
  }
}

const cache = new Map<string, Theme>();

/** Load a theme by name; cached per process. Throws ThemeError when it is
 * missing or malformed. */
export function loadTheme(name = "default"): Theme {
  const hit = cache.get(name);
  if (hit) return hit;
  if (!/^[a-z0-9][a-z0-9_-]*$/i.test(name)) throw new ThemeError(`theme name ${JSON.stringify(name)} is not a folder name`);
  const dir = themeDirs().map((d) => join(d, name)).find((d) => exists(join(d, "theme.json")));
  if (!dir) throw new ThemeError(`theme ${JSON.stringify(name)} not found (looked in ${themeDirs().join(", ")})`);
  let file: ThemeFile;
  try {
    file = JSON.parse(readFileSync(join(dir, "theme.json"), "utf8")) as ThemeFile;
  } catch (e) {
    throw new ThemeError(`${dir}/theme.json: ${(e as Error).message}`);
  }
  validate(file, dir);
  const loadedFonts: LoadedFont[] = file.fonts.map((f) => {
    const p = join(dir, f.file);
    if (!exists(p)) throw new ThemeError(`${dir}/theme.json: font file ${f.file} is missing`);
    const buf = readFileSync(p);
    return {
      name: f.family,
      weight: f.weight,
      style: f.style ?? "normal",
      data: buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer,
    };
  });
  const markPath = join(dir, file.wordmark.mark);
  if (!exists(markPath)) throw new ThemeError(`${dir}/theme.json: wordmark file ${file.wordmark.mark} is missing`);
  const theme: Theme = {
    ...file,
    name: file.name || name,
    dir,
    strings: { ...DEFAULT_STRINGS, ...(file.strings ?? {}) },
    loadedFonts,
    markSvg: readFileSync(markPath, "utf8"),
  };
  cache.set(name, theme);
  return theme;
}

/** What `GET /themes` answers: no file contents. */
export function describeTheme(name: string) {
  const t = loadTheme(name);
  return {
    name: t.name,
    description: t.description ?? "",
    palette: t.palette,
    roles: t.roles,
    fonts: t.fonts.map((f) => ({ family: f.family, weight: f.weight, style: f.style ?? "normal" })),
    wordmark: t.wordmark.text,
    handle: t.handle,
    site: t.site,
  };
}

/** The mark as a data URI in the given colour, for an `<img>`. */
export function markDataUri(theme: Theme, color: string): string {
  const svg = theme.markSvg.replaceAll("currentColor", color);
  return `data:image/svg+xml;base64,${Buffer.from(svg, "utf8").toString("base64")}`;
}
