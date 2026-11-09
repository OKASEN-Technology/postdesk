/**
 * The eight slide types (docs/themes.md "Slide types"), laid out for
 * satori: feed slides 1080x1350, Stories 1080x1920. The theme supplies the
 * palette, the three font roles (serif headlines, sans body, mono numbers
 * and labels), the wordmark and the call-to-action footer; nothing here is
 * a colour or a name.
 *
 * The field names are the contract with the Python side
 * (`src/postdesk/maker`), published as JSON schema in `schema/slide.json`;
 * change both together.
 *
 * Satori only lays out flexbox: every element with more than one child is
 * `display: flex`, and a headline with an italic accent is split into words
 * (`Words`) rather than nested inline spans.
 */

import { Fragment, h, type Node } from "./h.js";
import { markDataUri, type Theme } from "./theme.js";

export const FEED_SIZE = { width: 1080, height: 1350 };
export const STORY_SIZE = { width: 1080, height: 1920 };

export const SLIDE_TYPES = ["cover", "caption", "statement", "stat", "table", "label", "cta", "story"] as const;
export type SlideType = (typeof SLIDE_TYPES)[number];

export type SlideImage = { url: string; credit?: string | null; fit?: "cover" | "contain" };
export type Band = { label: string; detail: string; lo: number; hi: number; level: number };

/** `n` of `total` is kept for the record; no slide prints a counter
 * (Instagram draws its own pill in the same corner). */
type Chrome = { n?: number; total?: number; theme?: string };

export type CoverSlide = Chrome & {
  type: "cover";
  kicker: string;
  kicker_sub?: string | null;
  headline: string;
  sub: string;
  link?: string | null;
  image?: SlideImage | null;
  /** Without an image: a display-size number that fills the top panel. */
  figure?: string | null;
};
export type CaptionSlide = Chrome & {
  type: "caption";
  image: SlideImage;
  kicker?: string | null;
  text: string;
};
export type StatementSlide = Chrome & {
  type: "statement";
  background?: SlideImage | null;
  num?: string | null;
  kicker: string;
  headline: string;
  headline_em?: string | null;
  body: string;
  source?: string | null;
};
export type StatSlide = Chrome & {
  type: "stat";
  kicker: string;
  figure: string;
  figure_sub: string;
  note?: string | null;
  bands?: Band[] | null;
  scale?: { min: number; max: number; base?: number | null } | null;
  source?: string | null;
};
export type TableSlide = Chrome & {
  type: "table";
  kicker: string;
  headline: string;
  /** `value` (text) takes the place of the rank change when set. */
  rows: { rank: number; name: string; delta?: number | null; value?: string | null }[];
  footer_left?: string | null;
  footer_right?: string | null;
};
/** An image with a wall label under it (a subject, a product, a place), or,
 * without an image, a panel of facts over the label. */
export type LabelSlide = Chrome & {
  type: "label";
  tag: string;
  tag_tone: "up" | "down" | "new" | "neutral";
  kicker: string;
  name: string;
  line1: string;
  line2?: string | null;
  /** A line under the name: a place, a role, a date. */
  meta?: string | null;
  link?: string | null;
  image?: SlideImage | null;
  /** With an image: what it shows, set as a wall label. */
  work?: { title: string; meta?: string | null } | null;
  facts?: { label: string; value: string }[] | null;
};
export type CtaSlide = Chrome & {
  type: "cta";
  background?: SlideImage | null;
  kicker: string;
  headline: string;
  headline_em?: string | null;
  body: string;
  /** Override the theme's footer line and site. */
  footer_line?: string | null;
  site?: string | null;
};
export type StorySlide = Chrome & {
  type: "story";
  kicker: string;
  kicker_sub?: string | null;
  headline: string;
  line: string;
  image?: SlideImage | null;
  /** A set figure (a date, a price) shown as a tag, or filling the top
   * panel when there is no image. */
  figure?: string | null;
};
export type Slide = CoverSlide | CaptionSlide | StatementSlide | StatSlide | TableSlide | LabelSlide | CtaSlide | StorySlide;

export function sizeOf(slide: Slide): { width: number; height: number } {
  return slide.type === "story" ? STORY_SIZE : FEED_SIZE;
}

const pad2 = (n: number) => String(n).padStart(2, "0");
const fmtNum = (v: number) => Math.round(v).toLocaleString("en-US");

type Style = Record<string, string | number | undefined>;

/** Build the layouts for one theme. */
export function layouts(theme: Theme) {
  const C = theme.palette;
  const SERIF = theme.roles.serif;
  const SANS = theme.roles.sans;
  const MONO = theme.roles.mono;
  const S = theme.strings;

  const rgba = (hex: string, a: number) => {
    const n = parseInt(hex.replace("#", ""), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  };
  const PAPER_WASH = rgba(C.paper, 0.84);
  const DEEP_WASH = rgba(C.deep, 0.86);
  // The bottom of a full-bleed slide, darkened so light type reads on any
  // photograph; plus a lighter band at the top for the wordmark.
  const SCRIM =
    `linear-gradient(180deg, ${rgba(C.deep, 0.55)} 0%, ${rgba(C.deep, 0)} 16%, ${rgba(C.deep, 0)} 32%, ` +
    `${rgba(C.deep, 0.7)} 56%, ${rgba(C.deep, 0.95)} 100%)`;
  const STORY_SCRIM =
    `linear-gradient(180deg, ${rgba(C.deep, 0.6)} 0%, ${rgba(C.deep, 0)} 18%, ${rgba(C.deep, 0)} 40%, ` +
    `${rgba(C.deep, 0.75)} 60%, ${rgba(C.deep, 0.96)} 100%)`;

  /** A display-size mono figure. In a monospaced font "," and "." take a
   * whole cell, so "33,474" reads as "33 , 474" at 150px: pull them in. */
  function Figure({ text, size }: { text: string; size: number }): Node {
    return (
      <Fragment>
        {text.split(/([,.])/).map((part, i) =>
          part === "," || part === "." ? (
            <span key={i} style={{ marginLeft: -0.2 * size, marginRight: -0.16 * size }}>
              {part}
            </span>
          ) : part ? (
            <span key={i}>{part}</span>
          ) : null,
        )}
      </Fragment>
    );
  }

  function Wordmark({ color, scale = 1 }: { color: string; scale?: number }): Node {
    const w = (theme.wordmark.markWidth ?? 30) * scale;
    const hh = (theme.wordmark.markHeight ?? 30) * scale;
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8 * scale }}>
        <img src={markDataUri(theme, color)} width={w} height={hh} style={{ width: w, height: hh }} />
        {theme.wordmark.text ? (
          <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: 30 * scale, letterSpacing: 0.5, color, lineHeight: 1 }}>
            {theme.wordmark.text}
          </div>
        ) : null}
      </div>
    );
  }

  function Arrow({ size = 28, color }: { size?: number; color: string }): Node {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} stroke-width="1.8">
        <path d="M5 12h14M13 6l6 6-6 6" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    );
  }

  /** A headline with an optional italic, accent-coloured tail, word by word. */
  function Words({
    text, em, size, color, emColor, weight = 400, tracking = -2,
  }: {
    text: string; em?: string | null; size: number; color: string; emColor: string; weight?: number; tracking?: number;
  }): Node {
    const plain = text.split(/\s+/).filter(Boolean);
    const accent = (em ?? "").split(/\s+/).filter(Boolean);
    const word = (w: string, i: number, italic: boolean) => (
      <span
        key={`${italic ? "e" : "p"}${i}`}
        style={{
          fontFamily: SERIF,
          fontStyle: italic ? "italic" : "normal",
          fontWeight: italic ? 400 : weight,
          color: italic ? emColor : color,
          marginRight: size * 0.24,
        }}
      >
        {w}
      </span>
    );
    return (
      <div style={{ display: "flex", flexWrap: "wrap", fontSize: size, lineHeight: 1.04, letterSpacing: tracking }}>
        {plain.map((w, i) => word(w, i, false))}
        {accent.map((w, i) => word(w, i, true))}
      </div>
    );
  }

  function Kicker({
    text, color, sub, square, subColor = C.sub,
  }: { text: string; color: string; sub?: string | null; square?: boolean; subColor?: string }): Node {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 16, fontFamily: MONO, fontSize: 22, letterSpacing: 3, textTransform: "uppercase" }}>
        {square ? <div style={{ width: 14, height: 14, background: color }} /> : null}
        <span style={{ color, fontWeight: 600 }}>{text}</span>
        {sub ? <span style={{ color: subColor }}>{`· ${sub}`}</span> : null}
      </div>
    );
  }

  function Credit({ text, size = 18 }: { text: string; size?: number }): Node {
    return (
      <div
        style={{
          position: "absolute", right: 24, bottom: 20, display: "flex", padding: "8px 14px", maxWidth: 1000,
          background: rgba(C.deep, 0.82), fontFamily: MONO, fontSize: size, color: C.paper,
        }}
      >
        {text}
      </div>
    );
  }

  function Photo({ image, height }: { image: SlideImage; height: number }): Node {
    return (
      <img
        src={image.url}
        width={1080}
        height={height}
        style={{ position: "absolute", left: 0, top: 0, width: 1080, height, objectFit: image.fit ?? "cover" }}
      />
    );
  }

  // A texture under a slide's words: full-bleed, washed back towards the
  // slide's ground so the type stays legible.
  function Background({ image, height, wash }: { image: SlideImage; height: number; wash: string }): Node {
    return (
      <div style={{ position: "absolute", left: 0, top: 0, width: 1080, height, display: "flex" }}>
        <Photo image={image} height={height} />
        <div style={{ position: "absolute", left: 0, top: 0, width: 1080, height, background: wash }} />
      </div>
    );
  }

  function Footer({ left, right, dark, rule }: { left: Node; right: Node; dark?: boolean; rule?: number }): Node {
    return (
      <div
        style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          borderTop: `${rule ?? 1}px solid ${dark ? C.rule : C.deep}`, paddingTop: 22,
          fontFamily: MONO, fontSize: 20, color: dark ? C.muted : C.sub,
        }}
      >
        <span style={{ maxWidth: 860 }}>{left}</span>
        {right}
      </div>
    );
  }

  function ImageCredit({ text }: { text: string }): Node {
    return <div style={{ display: "flex", fontFamily: MONO, fontSize: 18, color: C.muted }}>{text}</div>;
  }

  // The image under an overlaid slide. "cover" fills the frame; "contain"
  // keeps the picture whole on the dark ground, in the frame above the
  // words (`room`: the height they need) so the scrim doesn't fall across it.
  function Overlaid({ image, height, room }: { image: SlideImage; height: number; room: number }): Node {
    if ((image.fit ?? "cover") === "cover") return <Photo image={image} height={height} />;
    const box = height - room;
    return (
      <img
        src={image.url}
        width={1080 - 2 * 72}
        height={box - 2 * 72}
        style={{ position: "absolute", left: 72, top: 72, width: 1080 - 2 * 72, height: box - 2 * 72, objectFit: "contain" }}
      />
    );
  }

  const Tags: Record<LabelSlide["tag_tone"], string> = { up: C.accentLight, down: C.down, new: C.highlight, neutral: C.stone };

  function Tag({ text, tone }: { text: string; tone: LabelSlide["tag_tone"] }): Node {
    return (
      <div style={{ display: "flex", padding: "5px 12px", background: Tags[tone], color: C.deep, fontWeight: 600, letterSpacing: 2 }}>
        {text}
      </div>
    );
  }

  // --- the slides -----------------------------------------------------------

  // With an image the cover is the image: full bleed, the headline over its
  // lower third. Without one it keeps the split layout, a dark figure or
  // kicker panel over the light headline.
  function Cover(s: CoverSlide): Node {
    if (!s.image) return SplitCover(s);
    return (
      <div style={{ position: "relative", display: "flex", width: 1080, height: 1350, background: C.deep, color: C.paper }}>
        <Overlaid image={s.image} height={1350} room={560} />
        <div style={{ position: "absolute", left: 0, top: 0, width: 1080, height: 1350, backgroundImage: SCRIM }} />
        <div style={{ position: "absolute", left: 72, top: 52, display: "flex" }}>
          <Wordmark color={C.paper} />
        </div>
        <div style={{ position: "absolute", left: 72, right: 72, bottom: 64, display: "flex", flexDirection: "column", gap: 26 }}>
          <Kicker text={s.kicker} sub={s.kicker_sub} color={C.accentLight} square subColor={C.mutedLight} />
          <Words text={s.headline} size={92} color={C.paper} emColor={C.accentLight} weight={500} tracking={-2} />
          <div style={{ display: "flex", maxWidth: 880, fontFamily: SANS, fontSize: 30, lineHeight: 1.35, color: C.mutedLight }}>
            {s.sub}
          </div>
          {s.image.credit ? <ImageCredit text={s.image.credit} /> : null}
        </div>
      </div>
    );
  }

  function SplitCover(s: CoverSlide): Node {
    const figureStyle: Style = s.figure
      ? { fontFamily: MONO, fontWeight: 500, fontStyle: "normal", fontSize: 150, letterSpacing: -5, color: C.paper }
      : { fontFamily: SERIF, fontWeight: 400, fontStyle: "italic", fontSize: 120, letterSpacing: -3, color: C.accentLight };
    return (
      <div style={{ display: "flex", flexDirection: "column", width: 1080, height: 1350, background: C.paper }}>
        <div style={{ display: "flex", flexDirection: "column", height: 770, background: C.deep, color: C.paper }}>
          <div style={{ display: "flex", padding: "52px 72px" }}>
            <Wordmark color={C.paper} />
          </div>
          <div style={{ display: "flex", flexGrow: 1, alignItems: "center", padding: "0 72px 40px", lineHeight: 1, ...figureStyle }}>
            {s.figure ? <Figure text={s.figure} size={150} /> : s.kicker}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", flexGrow: 1, gap: 24, padding: "52px 72px 56px" }}>
          <Kicker text={s.kicker} sub={s.kicker_sub} color={C.accent} square />
          <Words text={s.headline} size={84} color={C.deep} emColor={C.accent} weight={500} tracking={-1.5} />
          <div style={{ display: "flex", fontFamily: SANS, fontSize: 30, lineHeight: 1.3, color: C.ink }}>{s.sub}</div>
          <div style={{ flexGrow: 1 }} />
          <div style={{ display: "flex", alignSelf: "flex-end", alignItems: "center", gap: 12, fontFamily: MONO, fontWeight: 600, fontSize: 22, color: C.deep }}>
            {S.swipe} <Arrow color={C.deep} />
          </div>
        </div>
      </div>
    );
  }

  function Caption(s: CaptionSlide): Node {
    return (
      <div style={{ position: "relative", display: "flex", width: 1080, height: 1350, background: C.deep, color: C.paper }}>
        <Overlaid image={s.image} height={1350} room={380} />
        <div style={{ position: "absolute", left: 0, top: 0, width: 1080, height: 1350, backgroundImage: SCRIM }} />
        <div style={{ position: "absolute", left: 72, right: 72, bottom: 64, display: "flex", flexDirection: "column", gap: 22 }}>
          {s.kicker ? <Kicker text={s.kicker} color={C.accentLight} /> : null}
          <div style={{ display: "flex", maxWidth: 900, fontFamily: SANS, fontWeight: 500, fontSize: 44, lineHeight: 1.3 }}>
            {s.text}
          </div>
          {s.image.credit ? <ImageCredit text={s.image.credit} /> : null}
        </div>
      </div>
    );
  }

  function Statement(s: StatementSlide): Node {
    return (
      <div style={{ position: "relative", display: "flex", flexDirection: "column", width: 1080, height: 1350, padding: "48px 72px 56px", background: C.paper, color: C.deep }}>
        {s.background ? <Background image={s.background} height={1350} wash={PAPER_WASH} /> : null}
        <div style={{ display: "flex", flexDirection: "column", flexGrow: 1, justifyContent: "center", gap: 44 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 20, fontFamily: MONO, fontSize: 22, letterSpacing: 3, textTransform: "uppercase", color: C.accent, fontWeight: 600 }}>
            {s.num ? <span style={{ fontSize: 64, letterSpacing: 0, fontWeight: 500 }}>{s.num}</span> : null}
            <span>{s.kicker}</span>
          </div>
          <Words text={s.headline} em={s.headline_em} size={88} color={C.deep} emColor={C.accent} />
          <div style={{ display: "flex", maxWidth: 900, fontFamily: SANS, fontSize: 36, lineHeight: 1.4, color: C.ink }}>{s.body}</div>
        </div>
        <Footer left={s.source ?? ""} right={<Arrow color={C.deep} />} />
        {s.background?.credit ? <Credit text={s.background.credit} /> : null}
      </div>
    );
  }

  function BandRow({ band, x, base }: { band: Band; x: (v: number) => number; base?: number | null }): Node {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontFamily: MONO, fontSize: 22 }}>
          <span style={{ fontWeight: 600 }}>{band.label}</span>
          <span style={{ color: C.muted }}>{band.detail}</span>
        </div>
        <div style={{ position: "relative", display: "flex", height: 36, width: 936, background: C.deepRaised }}>
          <div style={{ position: "absolute", left: x(band.lo), top: 0, width: Math.max(4, x(band.hi) - x(band.lo)), height: 36, background: C.accentLight, opacity: 0.45 }} />
          <div style={{ position: "absolute", left: x(band.level) - 4, top: -6, width: 8, height: 48, background: C.paper }} />
          {base != null ? (
            <div style={{ position: "absolute", left: x(base), top: -10, width: 0, height: 56, borderLeft: `2px dashed ${C.muted}` }} />
          ) : null}
        </div>
      </div>
    );
  }

  function Stat(s: StatSlide): Node {
    const bands = s.bands ?? [];
    const scale = s.scale ?? (bands.length
      ? { min: Math.min(...bands.map((b) => b.lo)) * 0.95, max: Math.max(...bands.map((b) => b.hi)) * 1.05 }
      : { min: 0, max: 1 });
    const x = (v: number) => Math.round(((v - scale.min) / (scale.max - scale.min || 1)) * 936);
    const big = s.figure.length > 9 ? 130 : 176;
    return (
      <div style={{ display: "flex", flexDirection: "column", width: 1080, height: 1350, gap: 44, padding: "48px 72px 56px", background: C.deep, color: C.paper }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 24 }}>
          <Kicker text={s.kicker} color={C.accentLight} />
          <div style={{ display: "flex", fontFamily: MONO, fontWeight: 500, fontSize: big, lineHeight: 1, letterSpacing: -6 }}>
            <Figure text={s.figure} size={big} />
          </div>
          <div style={{ display: "flex", fontFamily: SANS, fontSize: 30, lineHeight: 1.35, color: C.mutedLight }}>{s.figure_sub}</div>
        </div>
        {s.note || bands.length ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 28, borderTop: `1px solid ${C.rule}`, paddingTop: 36 }}>
            {s.note ? <div style={{ display: "flex", fontFamily: SERIF, fontSize: 48, lineHeight: 1.15 }}>{s.note}</div> : null}
            {bands.map((b) => <BandRow band={b} x={x} base={s.scale?.base} />)}
            {bands.length ? (
              <div style={{ display: "flex", justifyContent: "space-between", fontFamily: MONO, fontSize: 18, color: C.muted }}>
                <span>{fmtNum(scale.min)}</span>
                <span style={{ display: "flex", alignItems: "center", gap: 24 }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span style={{ width: 28, height: 16, background: C.accentLight, opacity: 0.45 }} />
                    {S.band}
                  </span>
                  <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span style={{ width: 6, height: 22, background: C.paper }} />
                    {S.level}
                  </span>
                </span>
                <span>{fmtNum(scale.max)}</span>
              </div>
            ) : null}
          </div>
        ) : null}
        <div style={{ flexGrow: 1 }} />
        <div style={{ display: "flex", borderTop: `1px solid ${C.rule}`, paddingTop: 22, fontFamily: MONO, fontSize: 20, color: C.muted }}>
          {s.source ?? ""}
        </div>
      </div>
    );
  }

  function Delta({ delta }: { delta: number | null | undefined }): Node {
    if (delta === null || delta === undefined) {
      return (
        <div style={{ display: "flex", width: 120, justifyContent: "flex-end" }}>
          <div style={{ display: "flex", padding: "6px 14px", background: C.highlight, color: C.deep, fontFamily: MONO, fontSize: 22, fontWeight: 600, letterSpacing: 2 }}>
            {S.newEntry}
          </div>
        </div>
      );
    }
    if (delta === 0) {
      return <div style={{ display: "flex", width: 120, justifyContent: "flex-end", fontFamily: MONO, fontSize: 28, color: C.muted }}>=</div>;
    }
    const up = delta > 0;
    const color = up ? C.up : C.down;
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8, width: 120, justifyContent: "flex-end", fontFamily: MONO, fontSize: 28, color }}>
        <svg width="18" height="16" viewBox="0 0 18 16">
          <path d={up ? "M9 0L18 16H0z" : "M9 16L0 0H18z"} fill={color} />
        </svg>
        {Math.abs(delta)}
      </div>
    );
  }

  function Table(s: TableSlide): Node {
    return (
      <div style={{ display: "flex", flexDirection: "column", width: 1080, height: 1350, gap: 36, padding: "48px 72px 56px", background: C.deep, color: C.paper }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Kicker text={s.kicker} color={C.accentLight} />
          <div style={{ display: "flex", fontFamily: SERIF, fontSize: 76, lineHeight: 1.05, letterSpacing: -1.5 }}>{s.headline}</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", borderTop: `2px solid ${C.paper}` }}>
          {s.rows.map((r) => (
            <div key={r.rank} style={{ display: "flex", alignItems: "center", gap: 28, height: 78, borderBottom: `1px solid ${C.rule}` }}>
              <div style={{ display: "flex", width: 72, fontFamily: MONO, fontSize: 38, fontWeight: 500 }}>{pad2(r.rank)}</div>
              <div style={{ display: "flex", flexGrow: 1, fontFamily: SANS, fontSize: 36, fontWeight: 500 }}>{r.name}</div>
              {r.value ? (
                <div style={{ display: "flex", fontFamily: MONO, fontSize: 30, fontWeight: 500, color: C.accentLight }}>{r.value}</div>
              ) : (
                <Delta delta={r.delta} />
              )}
            </div>
          ))}
        </div>
        <div style={{ flexGrow: 1 }} />
        <Footer dark left={s.footer_left ?? ""} right={<span>{s.footer_right ?? ""}</span>} />
      </div>
    );
  }

  function FactsPanel({ facts }: { facts: { label: string; value: string }[] }): Node {
    return (
      <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap: 32, width: 1080, padding: "0 72px" }}>
        {facts.map((f) => (
          <div key={f.label} style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: `1px solid ${C.rule}`, paddingTop: 18 }}>
            <div style={{ display: "flex", fontFamily: MONO, fontSize: 22, letterSpacing: 3, textTransform: "uppercase", color: C.accentLight, fontWeight: 600 }}>
              {f.label}
            </div>
            <div style={{ display: "flex", fontFamily: MONO, fontSize: 56, fontWeight: 500, letterSpacing: -2, color: C.paper }}>{f.value}</div>
          </div>
        ))}
      </div>
    );
  }

  // The wall label: the picture's title in italics, who or what it is, a
  // line or two of why it is here, and the tag where a gallery would put
  // the lender. With an image the label is set over it (WallLabelOver);
  // without one the top is a dark panel of facts.
  function WallLabel(s: LabelSlide): Node {
    if (s.image) return WallLabelOver(s, s.image);
    const who = [s.name, s.meta].filter(Boolean).join(" · ");
    return (
      <div style={{ display: "flex", flexDirection: "column", width: 1080, height: 1350, background: C.paper }}>
        <div style={{ position: "relative", display: "flex", height: 640, background: C.deep }}>
          <FactsPanel facts={s.facts ?? []} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", flexGrow: 1, padding: "40px 72px 48px" }}>
          {s.work ? (
            <div style={{ display: "flex", fontFamily: SERIF, fontStyle: "italic", fontSize: 54, lineHeight: 1.05, letterSpacing: -1, color: C.deep }}>
              {s.work.title}
            </div>
          ) : null}
          <div style={{ display: "flex", marginTop: s.work ? 12 : 0, fontFamily: s.work ? SANS : SERIF, fontWeight: s.work ? 600 : 500, fontSize: s.work ? 28 : 64, letterSpacing: s.work ? 0 : -1.5, color: C.deep }}>
            {s.work ? who : s.name}
          </div>
          {s.work?.meta ? (
            <div style={{ display: "flex", marginTop: 6, fontFamily: MONO, fontSize: 20, color: C.sub }}>{s.work.meta}</div>
          ) : !s.work && s.meta ? (
            <div style={{ display: "flex", marginTop: 8, fontFamily: SANS, fontSize: 26, color: C.sub }}>{s.meta}</div>
          ) : null}
          <div style={{ display: "flex", marginTop: 20, maxWidth: 900, fontFamily: SANS, fontSize: 27, lineHeight: 1.4, color: C.ink }}>
            {[s.line1, s.line2].filter(Boolean).join(" ")}
          </div>
          <div style={{ flexGrow: 1 }} />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: `1px solid ${C.deep}`, paddingTop: 18, fontFamily: MONO, fontSize: 20, letterSpacing: 2, textTransform: "uppercase" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
              <Tag text={s.tag} tone={s.tag_tone} />
              {s.kicker.toLowerCase().includes(s.tag.toLowerCase()) ? null : <span style={{ color: C.sub }}>{s.kicker}</span>}
            </div>
            <span style={{ color: C.deep, textTransform: "none", letterSpacing: 0 }}>{s.link ?? ""}</span>
          </div>
        </div>
      </div>
    );
  }

  function WallLabelOver(s: LabelSlide, image: SlideImage): Node {
    const who = [s.name, s.meta].filter(Boolean).join(" · ");
    const lines = [s.line1, s.line2].filter(Boolean).join(" ");
    return (
      <div style={{ position: "relative", display: "flex", width: 1080, height: 1350, background: C.deep, color: C.paper }}>
        <Overlaid image={image} height={1350} room={440} />
        <div style={{ position: "absolute", left: 0, top: 0, width: 1080, height: 1350, backgroundImage: SCRIM }} />
        <div style={{ position: "absolute", left: 72, right: 72, bottom: 56, display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16, fontFamily: MONO, fontSize: 20, letterSpacing: 2, textTransform: "uppercase" }}>
            <Tag text={s.tag} tone={s.tag_tone} />
            {s.kicker.toLowerCase().includes(s.tag.toLowerCase()) ? null : <span style={{ color: C.mutedLight }}>{s.kicker}</span>}
          </div>
          {s.work ? (
            <div style={{ display: "flex", marginTop: 22, fontFamily: SERIF, fontStyle: "italic", fontSize: 64, lineHeight: 1.05, letterSpacing: -1, color: C.paper }}>
              {s.work.title}
            </div>
          ) : null}
          <div style={{ display: "flex", marginTop: s.work ? 14 : 22, fontFamily: s.work ? SANS : SERIF, fontWeight: s.work ? 600 : 500, fontSize: s.work ? 30 : 72, letterSpacing: s.work ? 0 : -1.5, color: C.paper }}>
            {s.work ? who : s.name}
          </div>
          {s.work?.meta ? (
            <div style={{ display: "flex", marginTop: 8, fontFamily: MONO, fontSize: 21, color: C.mutedLight }}>{s.work.meta}</div>
          ) : null}
          {lines ? (
            <div style={{ display: "flex", marginTop: 18, maxWidth: 900, fontFamily: SANS, fontSize: 28, lineHeight: 1.4, color: C.mutedLight }}>{lines}</div>
          ) : null}
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 26, fontFamily: MONO, fontSize: 18, color: C.muted }}>
            <span>{image.credit ?? ""}</span>
            <span style={{ color: C.mutedLight }}>{s.link ?? ""}</span>
          </div>
        </div>
      </div>
    );
  }

  function Label(s: LabelSlide): Node {
    if (s.image || s.work) return WallLabel(s);
    return (
      <div style={{ display: "flex", flexDirection: "column", width: 1080, height: 1350, background: C.paper }}>
        <div style={{ position: "relative", display: "flex", flexDirection: "column", height: 880, background: C.deep, color: C.paper }}>
          {s.facts?.length ? (
            <div style={{ display: "flex", flexDirection: "column", flexGrow: 1, justifyContent: "center", gap: 36, padding: "0 72px 60px" }}>
              {s.facts.map((f) => (
                <div key={f.label} style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: `1px solid ${C.rule}`, paddingTop: 20 }}>
                  <div style={{ display: "flex", fontFamily: MONO, fontSize: 22, letterSpacing: 3, textTransform: "uppercase", color: C.accentLight, fontWeight: 600 }}>
                    {f.label}
                  </div>
                  <div style={{ display: "flex", fontFamily: MONO, fontSize: 64, fontWeight: 500, letterSpacing: -2 }}>{f.value}</div>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ display: "flex", padding: "52px 72px" }}>
              <Wordmark color={C.paper} />
            </div>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", flexGrow: 1, gap: 18, padding: "40px 72px 52px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16, fontFamily: MONO, fontSize: 22, letterSpacing: 3, textTransform: "uppercase" }}>
            <Tag text={s.tag} tone={s.tag_tone} />
            <span style={{ color: C.sub }}>{s.kicker}</span>
          </div>
          <div style={{ display: "flex", fontFamily: SERIF, fontWeight: 500, fontSize: 76, lineHeight: 1, letterSpacing: -2, color: C.deep }}>{s.name}</div>
          <div style={{ display: "flex", flexDirection: "column", fontFamily: SANS, fontSize: 28, lineHeight: 1.35, color: C.ink }}>
            <span>{s.line1}</span>
            {s.line2 ? <span>{s.line2}</span> : null}
          </div>
          <div style={{ flexGrow: 1 }} />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: `2px solid ${C.deep}`, paddingTop: 20 }}>
            <span style={{ fontFamily: SANS, fontSize: 24, color: C.ink }}>{s.meta ?? ""}</span>
            <span style={{ fontFamily: MONO, fontSize: 19, color: C.deep }}>{s.link ?? ""}</span>
          </div>
        </div>
      </div>
    );
  }

  function Cta(s: CtaSlide): Node {
    const line = s.footer_line ?? theme.ctaLine;
    const site = s.site ?? theme.site;
    return (
      <div style={{ position: "relative", display: "flex", flexDirection: "column", width: 1080, height: 1350, padding: "48px 72px 64px", background: C.deep, color: C.paper }}>
        {s.background ? <Background image={s.background} height={1350} wash={DEEP_WASH} /> : null}
        <div style={{ display: "flex", flexDirection: "column", flexGrow: 1, justifyContent: "center", gap: 40 }}>
          <Kicker text={s.kicker} color={C.accentLight} />
          <Words text={s.headline} em={s.headline_em} size={108} color={C.paper} emColor={C.paper} tracking={-3} />
          <div style={{ display: "flex", maxWidth: 840, fontFamily: SANS, fontSize: 34, lineHeight: 1.35, color: C.mutedLight }}>{s.body}</div>
          <div style={{ display: "flex", alignSelf: "flex-start", alignItems: "center", gap: 14, padding: "20px 30px", border: `2px solid ${C.paper}`, fontFamily: MONO, fontSize: 26, fontWeight: 600, letterSpacing: 2, textTransform: "uppercase" }}>
            {S.linkInBio} <Arrow color={C.paper} />
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 36, borderTop: `1px solid ${C.rule}`, paddingTop: 36 }}>
          <div style={{ display: "flex", flexWrap: "wrap", fontFamily: SANS, fontSize: 28, lineHeight: 1.4, color: C.mutedLight }}>
            {line ? <span style={{ marginRight: 10 }}>{line}</span> : null}
            {site ? <span style={{ color: C.paper, fontWeight: 600 }}>{site}</span> : null}
          </div>
          <Wordmark color={C.paper} scale={1.9} />
        </div>
        {s.background?.credit ? <Credit text={s.background.credit} /> : null}
      </div>
    );
  }

  // Story safe zones: nothing in the top 250px or the bottom 340px, and a
  // clear slot above that for the link sticker.
  function Story(s: StorySlide): Node {
    if (s.image) return StoryOver(s, s.image);
    const figureStyle: Style = s.figure
      ? { fontFamily: MONO, fontStyle: "normal", fontWeight: 500, fontSize: 150, letterSpacing: -5, color: C.paper }
      : { fontFamily: SERIF, fontStyle: "italic", fontWeight: 400, fontSize: 120, letterSpacing: -3, color: C.accentLight };
    return (
      <div style={{ display: "flex", flexDirection: "column", width: 1080, height: 1920, background: C.paper }}>
        <div style={{ position: "relative", display: "flex", flexDirection: "column", height: 1060, background: C.deep, color: C.paper }}>
          <div style={{ position: "absolute", left: 72, top: 260, display: "flex" }}>
            <Wordmark color={C.paper} scale={1.15} />
          </div>
          <div style={{ position: "absolute", left: 72, right: 72, top: 420, display: "flex", lineHeight: 1, ...figureStyle }}>
            {s.figure ? <Figure text={s.figure} size={150} /> : s.kicker}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 26, padding: "56px 72px 0" }}>
          <Kicker text={s.kicker} sub={s.kicker_sub} color={C.accent} square />
          <Words text={s.headline} size={84} color={C.deep} emColor={C.accent} weight={500} tracking={-1.5} />
          <div style={{ display: "flex", fontFamily: SANS, fontSize: 32, lineHeight: 1.35, color: C.ink }}>{s.line}</div>
        </div>
      </div>
    );
  }

  // A Story frame over its image: the wordmark below Instagram's top bar,
  // the words above its reply bar, a set figure as a tag over the kicker.
  function StoryOver(s: StorySlide, image: SlideImage): Node {
    return (
      <div style={{ position: "relative", display: "flex", width: 1080, height: 1920, background: C.deep, color: C.paper }}>
        {/* Always full bleed: a whole picture set small in a 9:16 frame reads as a thumbnail. */}
        <Photo image={{ ...image, fit: "cover" }} height={1920} />
        <div style={{ position: "absolute", left: 0, top: 0, width: 1080, height: 1920, backgroundImage: STORY_SCRIM }} />
        <div style={{ position: "absolute", left: 72, top: 260, display: "flex" }}>
          <Wordmark color={C.paper} scale={1.15} />
        </div>
        <div style={{ position: "absolute", left: 72, right: 72, bottom: 300, display: "flex", flexDirection: "column", gap: 26 }}>
          {s.figure ? (
            <div style={{ display: "flex" }}>
              <div style={{ display: "flex", padding: "8px 16px", background: C.highlight, color: C.deep, fontFamily: MONO, fontWeight: 600, fontSize: 30, letterSpacing: 2, textTransform: "uppercase" }}>
                {s.figure}
              </div>
            </div>
          ) : null}
          <Kicker text={s.kicker} sub={s.kicker_sub} color={C.accentLight} square subColor={C.mutedLight} />
          <Words text={s.headline} size={88} color={C.paper} emColor={C.accentLight} weight={500} tracking={-1.5} />
          <div style={{ display: "flex", fontFamily: SANS, fontSize: 34, lineHeight: 1.35, color: C.mutedLight }}>{s.line}</div>
          {image.credit ? <ImageCredit text={image.credit} /> : null}
        </div>
      </div>
    );
  }

  function render(s: Slide): Node {
    switch (s.type) {
      case "cover": return Cover(s);
      case "caption": return Caption(s);
      case "statement": return Statement(s);
      case "stat": return Stat(s);
      case "table": return Table(s);
      case "label": return Label(s);
      case "cta": return Cta(s);
      case "story": return Story(s);
    }
  }

  return { render };
}
