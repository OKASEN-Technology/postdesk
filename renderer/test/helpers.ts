import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const FIXTURES = resolve(dirname(fileURLToPath(import.meta.url)), "fixtures");

/** A tiny PNG (4x4, solid) as a data URI, so the fixtures need no network. */
export const PHOTO =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAFklEQVQImWNkYPjPwMDAwMDAxAAFAAcSAQNY9Lw2AAAAAElFTkSuQmCC";

export function fixtureNames(): string[] {
  return readdirSync(FIXTURES).filter((f) => f.endsWith(".json")).sort();
}

export function fixture(name: string): Record<string, unknown> {
  const raw = readFileSync(resolve(FIXTURES, name), "utf8").replaceAll("IMAGE", PHOTO);
  return JSON.parse(raw) as Record<string, unknown>;
}

/** Width and height from a JPEG's SOF marker. */
export function jpegSize(buf: Buffer): { width: number; height: number } {
  if (buf[0] !== 0xff || buf[1] !== 0xd8) throw new Error("not a JPEG");
  let i = 2;
  while (i < buf.length) {
    if (buf[i] !== 0xff) throw new Error("bad marker");
    const marker = buf[i + 1];
    if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      return { height: buf.readUInt16BE(i + 5), width: buf.readUInt16BE(i + 7) };
    }
    i += 2 + buf.readUInt16BE(i + 2);
  }
  throw new Error("no SOF marker");
}
