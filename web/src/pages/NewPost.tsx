import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errorText, type Config } from "../api";
import { CARD, ErrorNote, INPUT, LABEL, PRIMARY } from "../ui";
import { CaptionChecks } from "./PostEditor";

interface Picked { file: File; preview: string; alt: string; credit: string }

/** Queue a hand-made post: pick the slide images in order, write the
 * caption and alt text, and it is filed as a draft with a recommended
 * time. Approving it is a separate step, on purpose. */
export default function NewPost({ config }: { config: Config }) {
  const navigate = useNavigate();
  const [slides, setSlides] = useState<Picked[]>([]);
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [story, setStory] = useState(false);
  const [sourceRef, setSourceRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function pick(files: FileList | null) {
    if (!files) return;
    const picked = Array.from(files).map((file) => ({ file, preview: URL.createObjectURL(file), alt: "", credit: "" }));
    setSlides((prev) => [...prev, ...picked]);
  }

  function move(i: number, step: number) {
    setSlides((prev) => {
      const j = i + step;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  const kind = story ? "story" : slides.length > 1 ? "carousel" : "image";
  const problems: string[] = [];
  if (slides.length === 0) problems.push("Pick at least one image.");
  if (story && slides.length > 1) problems.push("A Story is one image.");
  if (!story && slides.length > 20) problems.push("A carousel takes at most 20 images.");
  if (!title.trim()) problems.push("Give it a title (only you see it).");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (problems.length) return;
    setBusy(true);
    setError(null);
    try {
      const uploaded = [];
      for (const s of slides) {
        const a = await api.upload(s.file, s.credit);
        uploaded.push({ asset_id: a.id, alt_text: s.alt });
      }
      const post = await api.createPost({ kind, title: title.trim(), caption: story ? "" : caption, slides: uploaded,
                                          source_ref: sourceRef.trim() || null });
      navigate(`/posts/${post.id}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">New post</h1>
      <p className="mt-1 max-w-3xl text-sm text-ink-2">
        Slides in order (PNG, JPEG or WebP; 4:5 to 1.91:1 for the feed, 9:16 for a Story), a caption, alt text for each.
        It is filed as a draft with a recommended time; you approve it on the next screen.
      </p>
      <form onSubmit={submit} className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        <section className="space-y-3">
          <label className={`${CARD} block cursor-pointer border-dashed text-center text-sm text-ink-2 hover:bg-paper-2`}>
            {slides.length ? "Add more images" : "Choose the slide images"}
            <input type="file" accept="image/png,image/jpeg,image/webp" multiple className="hidden" onChange={(e) => pick(e.target.files)} />
          </label>
          <ol className="space-y-3">
            {slides.map((s, i) => (
              <li key={s.preview} className={`${CARD} flex gap-3`}>
                <img src={s.preview} alt="" className={`h-28 shrink-0 rounded object-cover ${story ? "aspect-[9/16]" : "aspect-[4/5]"}`} />
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex items-center justify-between text-xs text-ink-3">
                    <span>Slide {i + 1} · {s.file.name}</span>
                    <span className="flex gap-2">
                      <button type="button" onClick={() => move(i, -1)} disabled={i === 0} className="disabled:opacity-30">↑</button>
                      <button type="button" onClick={() => move(i, 1)} disabled={i === slides.length - 1} className="disabled:opacity-30">↓</button>
                      <button type="button" onClick={() => setSlides((p) => p.filter((_, j) => j !== i))} className="text-clay">remove</button>
                    </span>
                  </div>
                  {!story && (
                    <textarea value={s.alt} rows={2} placeholder="Alt text: what is on this slide" className={INPUT}
                              onChange={(e) => setSlides((p) => p.map((x, j) => (j === i ? { ...x, alt: e.target.value } : x)))} />
                  )}
                  <input value={s.credit} placeholder="Image credit, if the picture is not yours (kept with the file)" className={INPUT}
                         onChange={(e) => setSlides((p) => p.map((x, j) => (j === i ? { ...x, credit: e.target.value } : x)))} />
                </div>
              </li>
            ))}
          </ol>
        </section>
        <div className="space-y-4">
          <section className={`${CARD} space-y-3`}>
            <label className="block text-xs text-ink-2">
              Title (for the queue, never posted)
              <input value={title} onChange={(e) => setTitle(e.target.value)} className={INPUT} required />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={story} onChange={(e) => setStory(e.target.checked)} /> This is a Story
            </label>
            <label className="block text-xs text-ink-2">
              Source reference (optional; a post and its Story share one, so the Story follows the post)
              <input value={sourceRef} onChange={(e) => setSourceRef(e.target.value)} className={INPUT} placeholder="e.g. weekend-bake" />
            </label>
          </section>
          {!story && (
            <section className={CARD}>
              <p className={LABEL}>Caption</p>
              <textarea value={caption} onChange={(e) => setCaption(e.target.value)} rows={10} className={INPUT} />
              <CaptionChecks caption={caption} config={config} />
            </section>
          )}
          <section className={`${CARD} space-y-2`}>
            {problems.map((p) => <p key={p} className="text-xs text-ink-3">{p}</p>)}
            <ErrorNote text={error} />
            <button type="submit" disabled={busy || problems.length > 0} className={PRIMARY}>
              {busy ? "Uploading…" : `Queue this ${kind}`}
            </button>
          </section>
        </div>
      </form>
    </>
  );
}
