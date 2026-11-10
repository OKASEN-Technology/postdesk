import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorText, type CardDetail, type Config } from "../api";

function Icon({ children }: { children: React.ReactNode }) {
  return <svg aria-hidden="true" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{children}</svg>;
}

export default function Preview({ config }: { config: Config }) {
  const { id = "" } = useParams();
  const [card, setCard] = useState<CardDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [slide, setSlide] = useState(0);
  const [broken, setBroken] = useState(false);

  useEffect(() => {
    let active = true;
    api.card(id).then(c => { if (active) setCard(c); }).catch(e => { if (active) setError(errorText(e)); });
    return () => { active = false; };
  }, [id]);

  if (error) return <p role="alert" className="mx-auto max-w-xl text-sm text-clay">Could not open this preview: {error}</p>;
  if (!card) return <p className="mx-auto max-w-xl text-sm text-ink-3">Loading preview…</p>;

  const draft = card.drafts.find(d => d.kind !== "story" && d.status !== "rejected")
    ?? card.drafts.find(d => d.status !== "rejected");
  const concept = !draft;
  const planned = card.plan.post?.slides ?? [];
  const proposedImages = card.images.filter(i => i.used && i.url);
  const images: { url: string | null; alt: string; description?: string }[] = draft
    ? draft.slides.map(s => ({ url: s.url, alt: s.alt_text || "Post slide" }))
    : planned.length
      ? planned.map((s, i) => ({ url: proposedImages[i]?.url ?? null, alt: proposedImages[i]?.what || "Proposed image", description: s.brief }))
      : proposedImages.map(i => ({ url: i.url, alt: i.what || "Proposed image" }));
  const current = images[Math.min(slide, Math.max(images.length - 1, 0))];
  const handle = config.handle || "your account";
  const caption = draft?.caption?.trim();
  const changeSlide = (next: number) => { setSlide(next); setBroken(false); };

  return <div className="mx-auto max-w-[470px] pb-10">
    <Link to={`/cards/${card.id}`} className="text-sm text-ink-2 hover:text-ink hover:underline">← Back to idea</Link>
    <div className="mb-5 mt-5">
      <p className="text-xs font-semibold uppercase tracking-[0.12em] text-moss">{concept ? "Concept preview" : "Draft preview"}</p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight">Instagram preview</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-2">{concept
        ? "A first look at the planned slides and any selected images. The final slides and caption are created after you approve the idea."
        : "A preview of your draft. Instagram may display it a little differently."}</p>
    </div>
    <article aria-label="Instagram post preview" className="overflow-hidden rounded-lg border border-line bg-white text-[#171717] shadow-sm">
      <div className="flex items-center gap-3 px-4 py-3">
        <div aria-hidden="true" className="grid h-9 w-9 place-items-center rounded-full bg-[#292929] text-sm font-semibold uppercase text-white">{handle.charAt(0)}</div>
        <div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold">{handle}</p><p className="text-xs text-neutral-500">Preview · Not published</p></div>
        <span aria-hidden="true" className="text-xl leading-none">···</span>
      </div>
      <div className="relative grid aspect-[4/5] place-items-center overflow-hidden bg-[#edeae5]">
        {current?.url && !broken ? <img src={current.url} alt={current.alt} onError={() => setBroken(true)} className="h-full w-full object-contain" />
          : <div className="max-w-[19rem] px-7 text-center"><p className="text-xs font-semibold uppercase tracking-[0.12em] text-neutral-500">{concept && current?.description ? `Slide ${slide + 1} idea` : broken ? "Image unavailable" : "Image not selected yet"}</p><p className="mt-3 whitespace-pre-wrap text-base leading-relaxed text-neutral-700">{concept && current?.description ? current.description : concept ? "The visual will appear here when one is chosen for the idea." : "This slide could not be displayed in the preview."}</p></div>}
        {images.length > 1 && <>
          <span className="absolute right-3 top-3 rounded-full bg-black/65 px-2.5 py-1 text-xs text-white">{slide + 1} / {images.length}</span>
          {slide > 0 && <button type="button" aria-label="Previous slide" onClick={() => changeSlide(slide - 1)} className="absolute left-3 grid h-9 w-9 place-items-center rounded-full bg-white/90 text-xl shadow">‹</button>}
          {slide < images.length - 1 && <button type="button" aria-label="Next slide" onClick={() => changeSlide(slide + 1)} className="absolute right-3 grid h-9 w-9 place-items-center rounded-full bg-white/90 text-xl shadow">›</button>}
        </>}
      </div>
      <div className="px-4 pb-5 pt-3">
        <div aria-hidden="true" className="flex items-center gap-4">
          <Icon><path d="M20.8 4.6a5.2 5.2 0 0 0-7.3 0L12 6.1l-1.5-1.5a5.2 5.2 0 0 0-7.3 7.3L12 20.7l8.8-8.8a5.2 5.2 0 0 0 0-7.3Z" /></Icon>
          <Icon><path d="M21 11.5a8.8 8.8 0 0 1-9 8.5 9.7 9.7 0 0 1-3.3-.6L4 21l1.2-4A8.2 8.2 0 0 1 3 11.5a9 9 0 0 1 18 0Z" /></Icon>
          <Icon><path d="m22 2-7.5 20-3.2-9.3L2 9.5 22 2ZM11.3 12.7 22 2" /></Icon>
          <span className="ml-auto"><Icon><path d="M5 3h14v18l-7-4-7 4V3Z" /></Icon></span>
        </div>
        {images.length > 1 && <p className="mt-2 text-center text-xs text-neutral-400" aria-label={`Slide ${slide + 1} of ${images.length}`}>{images.map((_, i) => <span key={i} className={i === slide ? "text-blue-500" : "text-neutral-300"}>● </span>)}</p>}
        <div className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed"><span className="font-semibold">{handle}</span>{" "}{caption || (concept ? "Caption to be written after the idea is approved." : "No caption for this draft.")}</div>
      </div>
    </article>
    {concept && <section className="mt-5 rounded-lg border border-line bg-paper px-4 py-4"><h2 className="text-sm font-semibold">The idea</h2><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-2">{card.brief_md || card.body_md || card.title}</p></section>}
    <Link to={`/cards/${card.id}`} className="mt-5 inline-block text-sm font-medium text-moss hover:underline">Return to the idea to approve it or request changes →</Link>
    <p className="mt-4 text-xs leading-relaxed text-ink-3">This is a private Postdesk preview. Opening it does not publish or approve anything.</p>
  </div>;
}
