import { describe, expect, it } from "vitest";

import { render, RenderError, renderSvg } from "../src/render.js";
import { FEED_SIZE, SLIDE_TYPES, STORY_SIZE, type Slide } from "../src/slides.js";
import { loadTheme } from "../src/theme.js";
import { fixture, fixtureNames, jpegSize } from "./helpers.js";

describe("render", () => {
  const names = fixtureNames();

  it("has a fixture for every slide type", () => {
    const covered = new Set(names.map((n) => (fixture(n) as { type: string }).type));
    for (const t of SLIDE_TYPES) expect(covered, `no fixture for ${t}`).toContain(t);
  });

  it.each(names)("renders %s as a JPEG of the right size", async (name) => {
    const spec = fixture(name);
    const out = await render(spec);
    const size = jpegSize(out);
    const want = spec.type === "story" ? STORY_SIZE : FEED_SIZE;
    expect(size).toEqual({ width: want.width, height: want.height });
    expect(out.length).toBeGreaterThan(20_000);
  });

  it("keeps the footer inside the slide", async () => {
    // Padding counts inside the height (border-box); a content-box root
    // would push the source line past 1350.
    const svg = await renderSvg(fixture("statement.json") as unknown as Slide, loadTheme());
    const ys = [...svg.matchAll(/<path[^>]*transform="[^"]*"[^>]*d="M([\d.]+) ([\d.]+)/g)].map((m) => Number(m[2]));
    expect(Math.max(...ys)).toBeLessThanOrEqual(FEED_SIZE.height);
  });

  it("rejects a bad spec with a 400 and a readable message", async () => {
    await expect(render({ type: "statement", kicker: "x" })).rejects.toThrow(RenderError);
    await expect(render({ type: "statement", kicker: "x" })).rejects.toThrow(/missing headline/);
    await expect(render({ type: "poster" })).rejects.toThrow(RenderError);
  });

  it("rejects an unknown theme", async () => {
    await expect(render(fixture("cover.json"), { theme: "nope" })).rejects.toThrow(/theme/i);
  });

  it("can return the SVG instead", async () => {
    const out = await render(fixture("cta.json"), { svg: true });
    expect(out.toString("utf8")).toMatch(/^<svg/);
  });

  it("renders the Vigil theme without the default template footer", async () => {
    const theme = loadTheme("vigil");
    expect(theme.wordmark.text).toBe("VIGIL");
    expect(theme.ctaLine).toBe("");
    expect(theme.strings.linkInBio).toBe("");
    expect(await render(fixture("cta.json"), { theme: "vigil" })).toBeTruthy();
  });
});
