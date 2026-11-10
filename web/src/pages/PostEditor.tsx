import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorText, type Config, type Lint, type Post, type SlidePatch } from "../api";
import { fmt, fromLocalInput, insidePublisherHours, toLocalInput } from "../time";
import { Badge, CARD, DANGER, ErrorNote, INPUT, LABEL, PRIMARY, SECONDARY } from "../ui";

const MAX_CAPTION = 2200;
const MORE_CUT = 125;
/** The fields that read better in a box than on a line. */
const LONG_FIELDS = new Set(["body", "text", "line", "sub", "figure_sub", "line1", "line2"]);
const FIELD_NAMES: Record<string, string> = {
  kicker: "Kicker", kicker_sub: "After the kicker", headline: "Headline", headline_em: "Headline, in italic",
  sub: "Line under the headline", body: "Body", source: "Source line", figure: "Figure", figure_sub: "What the figure is",
  note: "Note", line1: "First line", line2: "Second line", line: "Line", text: "Text over the photo",
};

/** The words on a made slide, editable; saving re-renders that slide. */
function SlideWords({ words, onChange }: { words: Record<string, string>; onChange: (field: string, value: string) => void }) {
  return (
    <div className="mt-2 space-y-2">
      {Object.entries(words).map(([field, value]) => (
        <label key={field} className="block text-xs text-ink-2">
          {FIELD_NAMES[field] ?? field}
          {LONG_FIELDS.has(field) ? (
            <textarea value={value} rows={2} className={INPUT} onChange={(e) => onChange(field, e.target.value)} />
          ) : (
            <input value={value} className={INPUT} onChange={(e) => onChange(field, e.target.value)} />
          )}
        </label>
      ))}
    </div>
  );
}

/** The caption's checks: the server's lint (the same one the queue
 * applies) plus what only matters in the feed. */
export function CaptionChecks({ caption, config }: { caption: string; config: Config }) {
  const [lint, setLint] = useState<Lint | null>(null);
  useEffect(() => {
    const t = setTimeout(() => api.lint(caption).then(setLint).catch(() => setLint(null)), 300);
    return () => clearTimeout(t);
  }, [caption]);
  return (
    <div className="mt-1 space-y-2 text-xs">
      <p className="font-mono text-[11px] text-ink-3">
        <span className={caption.length > MAX_CAPTION ? "text-clay" : ""}>{caption.length} / {MAX_CAPTION}</span>
        {lint && <> · <span className={lint.hashtags > config.max_hashtags ? "text-sun" : ""}>{lint.hashtags} hashtag{lint.hashtags === 1 ? "" : "s"}</span></>}
      </p>
      {lint?.errors.map((e) => <p key={e} className="text-clay">{e}. Publishing would fail.</p>)}
      {lint?.warnings.map((w) => <p key={w} className="text-sun">{w}</p>)}
      {/https?:\/\/|www\./i.test(caption) && <p className="text-sun">Links are not clickable in Instagram captions.</p>}
      <div className="rounded bg-paper-2 p-2">
        <p className={LABEL}>Shown before “… more” in the feed</p>
        <p className="mt-1 whitespace-pre-wrap break-words text-sm text-ink-2">
          {caption.slice(0, MORE_CUT)}{caption.length > MORE_CUT && <span className="text-ink-3">… more</span>}
        </p>
      </div>
    </div>
  );
}

export default function PostEditor({ config }: { config: Config }) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const tz = config.timezone;
  const [post, setPost] = useState<Post | null>(null);
  const [caption, setCaption] = useState("");
  const [title, setTitle] = useState("");
  const [alts, setAlts] = useState<string[]>([]);
  const [words, setWords] = useState<(Record<string, string> | null)[]>([]);
  const [slide, setSlide] = useState(0);
  const [publishAt, setPublishAt] = useState("");
  const [crosspostX, setCrosspostX] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((p: Post) => {
    setPost(p);
    setCaption(p.caption);
    setTitle(p.title);
    setAlts(p.slides.map((s) => s.alt_text));
    setWords(p.slides.map((s) => (s.words ? { ...s.words } : null)));
    setPublishAt(toLocalInput(p.publish_at ?? new Date(Date.now() + 86400000).toISOString(), tz));
  }, [tz]);

  useEffect(() => {
    api.getPost(id).then(load).catch((e) => setError(errorText(e)));
  }, [id, load]);

  const count = post?.slides.length ?? 0;
  const go = useCallback((step: number) => setSlide((i) => Math.min(count - 1, Math.max(0, i + step))), [count]);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (t && ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) return;
      if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "ArrowRight") go(1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go]);

  if (error && !post) return <ErrorNote text={error} />;
  if (!post) return <p className="text-sm text-ink-3">Loading…</p>;

  const editable = post.status === "draft" && post.channel !== "x";
  const schedulable = post.status === "draft";
  const wordPatches: SlidePatch[] = words.flatMap((w, i) => {
    const was = post.slides[i]?.words;
    if (!w || !was) return [];
    const fields = Object.fromEntries(Object.entries(w).filter(([k, v]) => v !== (was[k] ?? "")));
    return Object.keys(fields).length ? [{ index: i, fields }] : [];
  });
  const dirty = caption !== post.caption || title !== post.title || alts.some((a, i) => a !== post.slides[i]?.alt_text)
    || wordPatches.length > 0;
  const tall = post.kind === "story" || post.kind === "reel";
  const current = post.slides[slide];
  const missingAlt = post.kind === "story" || post.kind === "reel" ? 0 : alts.filter((a) => !a.trim()).length;
  const chosen = publishAt ? fromLocalInput(publishAt, tz) : null;
  const inPast = chosen !== null && chosen.getTime() < Date.now();
  const outsideHours = publishAt !== "" && !insidePublisherHours(publishAt, config.publisher_hours);

  async function run(fn: () => Promise<Post>, then?: () => void) {
    setBusy(true);
    setError(null);
    try {
      load(await fn());
      then?.();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const edits = () => ({
    title, caption: post.kind === "story" ? undefined : caption, alt_texts: alts,
    slides: wordPatches.length ? wordPatches : undefined,
  });
  const save = () => run(() => api.updatePost(post.id, edits()));
  // Back to where the post belongs: its card on the board, else the queue.
  const home = post.card_id ? `/cards/${post.card_id}` : "/queue";
  const approve = () => run(async () => {
    if (dirty) await api.updatePost(post.id, edits());
    return api.approve(post.id, fromLocalInput(publishAt, tz).toISOString(), crosspostX);
  }, () => navigate(home));

  return (
    <>
      <nav className="flex gap-3 text-xs">
        {post.card_id && <Link to={home} className="text-moss hover:underline">← Card</Link>}
        <Link to="/queue" className="text-moss hover:underline">{post.card_id ? "Queue" : "← Queue"}</Link>
      </nav>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {editable ? (
            <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Title"
                   className="w-full max-w-xl rounded border border-transparent bg-transparent text-2xl font-semibold tracking-tight hover:border-line focus:border-line focus:outline-none" />
          ) : (
            <h1 className="text-2xl font-semibold tracking-tight">{post.title}</h1>
          )}
          <p className="text-sm text-ink-2">{post.kind} · {count} slide{count === 1 ? "" : "s"} · {post.channel}</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-ink-2">
          <Badge status={post.status} />
          {post.status === "approved" && fmt(post.publish_at, tz)}
          {post.status === "published" && fmt(post.published_at, tz)}
        </div>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        <section aria-label="Slides" className="min-w-0">
          <div className={`relative mx-auto overflow-hidden rounded-lg border border-line bg-paper-2 ${tall ? "aspect-[9/16] w-[min(100%,calc(75vh*0.5625))]" : "aspect-[4/5] w-[min(100%,calc(75vh*0.8))]"}`}>
            {current?.url ? post.kind === "reel" ? <video src={current.url} controls className="h-full w-full object-contain" /> : <img src={current.url} alt={alts[slide] ?? ""} className="h-full w-full object-contain" /> : <p className="p-4 text-sm text-ink-3">No image</p>}
            {count > 1 && (
              <>
                <button type="button" onClick={() => go(-1)} disabled={slide === 0} aria-label="Previous slide"
                        className="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-white/90 px-3 py-1 text-lg shadow disabled:opacity-30">‹</button>
                <button type="button" onClick={() => go(1)} disabled={slide === count - 1} aria-label="Next slide"
                        className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-white/90 px-3 py-1 text-lg shadow disabled:opacity-30">›</button>
              </>
            )}
          </div>
          <p className="mt-2 text-center font-mono text-xs text-ink-2">
            Slide {slide + 1} / {count}{count > 1 && " · ← → to move"}
            {missingAlt > 0 && <span className="text-clay"> · {missingAlt} without alt text</span>}
          </p>
          {count > 1 && (
            <ol className="mx-auto mt-3 flex w-fit max-w-full gap-2 overflow-x-auto p-1">
              {post.slides.map((s, i) => (
                <li key={s.asset_id + i} className="shrink-0">
                  <button type="button" onClick={() => setSlide(i)} aria-current={i === slide} aria-label={`Slide ${i + 1}`}
                          className={`relative block h-16 overflow-hidden rounded border bg-paper-2 aspect-[4/5] ${i === slide ? "border-ink ring-2 ring-ink" : "border-line opacity-70 hover:opacity-100"}`}>
                    {s.url && <img src={s.url} alt="" className="h-full w-full object-cover" />}
                    {!alts[i]?.trim() && <span title="No alt text" className="absolute right-0.5 top-0.5 h-2.5 w-2.5 rounded-full border border-white bg-clay" />}
                  </button>
                </li>
              ))}
            </ol>
          )}
        </section>

        <div className="min-w-0 space-y-4">
          {current?.words && (
            <section className={CARD}>
              <p className={LABEL}>Words on slide {slide + 1} · {String(current.spec?.type ?? "")}</p>
              {editable ? (
                <>
                  <SlideWords words={words[slide] ?? {}}
                              onChange={(field, value) => setWords((prev) => prev.map((w, j) => (j === slide && w ? { ...w, [field]: value } : w)))} />
                  <p className="mt-2 text-xs text-ink-3">
                    Saving re-renders this slide. A number that is not in the source is refused; pictures, rows and links
                    need a new make.
                  </p>
                </>
              ) : (
                <dl className="mt-1 space-y-1 text-sm text-ink-2">
                  {Object.entries(current.words).filter(([, v]) => v).map(([k, v]) => (
                    <div key={k}><dt className="inline text-xs text-ink-3">{FIELD_NAMES[k] ?? k}: </dt><dd className="inline">{v}</dd></div>
                  ))}
                </dl>
              )}
            </section>
          )}

          {post.kind !== "reel" && <section className={CARD}>
            <p className={LABEL}>Alt text · slide {slide + 1}</p>
            {editable ? (
              <textarea value={alts[slide] ?? ""} rows={3} placeholder="Describe what is on this slide" className={INPUT}
                        onChange={(e) => setAlts((prev) => prev.map((a, j) => (j === slide ? e.target.value : a)))} />
            ) : (
              <p className="mt-1 text-sm text-ink-2">{current?.alt_text || <em>No alt text</em>}</p>
            )}
            {editable && post.kind !== "story" && !alts[slide]?.trim() && <p className="mt-1 text-xs text-clay">This slide has no alt text.</p>}
            {post.kind === "story" && <p className="mt-1 text-xs text-ink-3">Instagram does not take alt text on Stories; it is kept for your records.</p>}
          </section>}

          {post.kind !== "story" && (
            <section className={CARD}>
              <p className={LABEL}>Caption</p>
              {editable ? (
                <textarea value={caption} onChange={(e) => setCaption(e.target.value)} rows={10} className={INPUT} />
              ) : (
                <p className="mt-1 whitespace-pre-wrap break-words text-sm text-ink-2">{post.caption}</p>
              )}
              <CaptionChecks caption={caption} config={config} />
            </section>
          )}

          <section className={CARD}>
            <p className={LABEL}>Schedule</p>
            <div className="mt-2 space-y-3">
              <ErrorNote text={error} />
              {schedulable && (
                <>
                  {post.channel !== "instagram" && <p className="text-xs text-ink-2">This copy keeps the same content as its Instagram post.</p>}
                  {post.channel === "instagram" && <p className="text-sm text-ink-2">Instagram and Facebook are scheduled together. <Link to="/settings" className="text-moss underline">Facebook settings</Link></p>}
                  <label className="block text-xs text-ink-2">
                    Publish at ({tz})
                    {post.publish_at && publishAt === toLocalInput(post.publish_at, tz) && " · recommended"}
                    <input type="datetime-local" value={publishAt} onChange={(e) => setPublishAt(e.target.value)}
                           className="mt-1 block rounded border border-line bg-white px-2 py-1 text-sm" />
                  </label>
                  {inPast && <p className="text-xs text-sun">This time has passed, so it goes out on the next publisher pass once approved.</p>}
                  {outsideHours && (
                    <p className="text-xs text-sun">
                      The publisher only posts between {config.publisher_hours[0]} and {config.publisher_hours[1]} ({tz}),
                      so this goes out at {config.publisher_hours[0]} instead.
                    </p>
                  )}
                  {post.channel === "instagram" && post.kind !== "story" && post.kind !== "reel" && <div className="rounded border border-line p-3">
                    <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={crosspostX} onChange={(e) => setCrosspostX(e.target.checked)} className="mt-1" /> Also post to X</label>
                    <p className="mt-1 text-xs text-ink-2">Uses the same caption and images. X allows up to four images; both platform limits are checked when scheduling. Image alt text is not sent to X. <Link to="/settings" className="text-moss underline">Connect X</Link></p>
                  </div>}
                  <div className="flex flex-wrap gap-2">
                    <button type="button" disabled={busy || !publishAt} onClick={approve} className={PRIMARY}>Approve &amp; schedule</button>
                    {dirty && <button type="button" disabled={busy} onClick={save} className={SECONDARY}>Save edits</button>}
                    <button type="button" disabled={busy} onClick={() => run(() => api.action(post.id, "reject"), () => navigate(home))} className={DANGER}>Reject</button>
                  </div>
                </>
              )}
              {post.status === "approved" && (
                <>
                  <p className="text-sm text-ink-2">Goes out {fmt(post.publish_at, tz)} · approved by {post.approved_by}. An approved post cannot be edited.</p>
                  <div className="flex flex-wrap gap-2">
                    <button type="button" disabled={busy} onClick={() => run(() => api.action(post.id, "unapprove"))} className={SECONDARY}>Unapprove to edit</button>
                    <button type="button" disabled={busy} onClick={() => run(() => api.action(post.id, "reject"), () => navigate(home))} className={DANGER}>Reject</button>
                  </div>
                </>
              )}
              {post.status === "publishing" && (
                <>
                  <p className="text-sm text-ink-2">
                    Publishing started {fmt(post.updated_at, tz)}. If that is more than an hour ago, check the account on
                    {post.channel === "x" ? "X" : post.channel === "facebook" ? "Facebook" : "Instagram"} first: send it back to draft only if it never went out, or you will post it twice.
                  </p>
                  <button type="button" disabled={busy} onClick={() => run(() => api.action(post.id, "back-to-draft"))} className={SECONDARY}>Back to draft</button>
                </>
              )}
              {post.status === "failed" && (
                <div className="flex flex-wrap gap-2">
                  <button type="button" disabled={busy} onClick={() => run(() => api.action(post.id, "back-to-draft"))} className={SECONDARY}>Back to draft</button>
                  <button type="button" disabled={busy} onClick={() => run(() => api.action(post.id, "reject"), () => navigate(home))} className={DANGER}>Reject</button>
                </div>
              )}
              {(post.status === "published" || post.status === "rejected") && <p className="text-sm text-ink-2">Nothing left to schedule.</p>}
            </div>
          </section>

          <section className={CARD}>
            <p className={LABEL}>History</p>
            <ol className="mt-2 space-y-1 text-sm text-ink-2">
              <li>Created {fmt(post.created_at, tz)} by {post.created_by}{post.source_ref && <> · <code className="text-xs">{post.source_ref}</code></>}</li>
              {post.approved_at && <li>Approved {fmt(post.approved_at, tz)} by {post.approved_by}</li>}
              {post.status === "publishing" && <li>Publishing since {fmt(post.updated_at, tz)}</li>}
              {post.published_at && (
                <li>
                  Published {fmt(post.published_at, tz)}
                  {post.permalink && <> · <a href={post.permalink} target="_blank" rel="noreferrer" className="text-moss hover:underline">View on {post.channel === "x" ? "X" : post.channel === "facebook" ? "Facebook" : "Instagram"}</a></>}
                </li>
              )}
              {post.status === "rejected" && <li>Rejected {fmt(post.updated_at, tz)}</li>}
              {post.insights && (
                <li>
                  Insights {fmt(post.insights_at, tz)}:{" "}
                  {Object.entries(post.insights).map(([k, v]) => `${k} ${v}`).join(" · ")}
                </li>
              )}
            </ol>
            {post.error && <p className="mt-3 whitespace-pre-wrap break-words rounded bg-clay-2 p-2 text-sm text-clay">{post.error}</p>}
          </section>
        </div>
      </div>
    </>
  );
}
