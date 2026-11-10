import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorText, type CardDetail, type Config, type Connections } from "../api";
import { fmt, fromLocalInput, toLocalInput } from "../time";
import { Badge, CARD, DANGER, ErrorNote, INPUT, LABEL, PRIMARY, SECONDARY } from "../ui";

const POLL_MS = 3000;
const PARTNER = ["Planned", "Brief ready"];
const MADE = ["In review", "Awaiting approval", "On hold"];

const AUTHOR: Record<string, string> = { user: "You", partner: "Partner", editor: "Editor", maker: "Maker", system: "postdesk" };

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className={CARD}>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className={LABEL}>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** Markdown is shown as the text it is: the brief and the thread are short and plain. */
function Text({ md }: { md: string }) {
  return <div className="whitespace-pre-wrap break-words text-sm leading-relaxed text-ink">{md}</div>;
}

function Actions({ card, config, onDone }: { card: CardDetail; config: Config; onDone: (c: CardDetail) => void }) {
  const navigate = useNavigate();
  const tz = config.timezone;
  const [note, setNote] = useState("");
  const [connections, setConnections] = useState<Connections | null>(null);
  const [connectionError, setConnectionError] = useState(false);
  useEffect(() => { api.connections().then(setConnections).catch(() => setConnectionError(true)); }, []);
  const [when, setWhen] = useState(card.recommended_at ? toLocalInput(card.recommended_at, tz) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (card.recommended_at && !when) setWhen(toLocalInput(card.recommended_at, tz)); },
            [card.recommended_at, tz, when]);

  const move = (column: string, opts: { note?: string; when?: string; ask?: string } = {}) => {
    if (opts.ask && !window.confirm(opts.ask)) return;
    setBusy(true);
    setError(null);
    api.move(card.id, column, opts.note, opts.when)
      .then((c) => { setNote(""); onDone(c); if (c.closed) navigate("/"); })
      .catch((e) => setError(errorText(e)))
      .finally(() => setBusy(false));
  };
  const col = card.column;
  const hasDrafts = card.drafts.some((d) => d.status === "draft");
  const canAcceptDraft = col === "On hold" && hasDrafts &&
    (["Image updated. Review the current draft before scheduling.", "Draft updated. Review the changes before scheduling."].includes(card.stuck_reason || "") ||
     (card.review?.column === "On hold" && !!card.review?.why &&
      (card.stuck_reason === card.review.why || !!card.stuck_reason?.startsWith(`${card.review.why}:`))));
  const needNote = () => { if (!note.trim()) { setError("Write what to change first."); return false; } return true; };

  if (card.closed) return <p className="text-sm text-ink-2">This card is closed.</p>;
  return (
    <div className="space-y-3">
      {col === "Awaiting approval" && (
        <div className="space-y-2 rounded border border-moss bg-moss-2 p-3">
          <p className="text-sm font-medium">Publishing readiness</p>
          <p className="text-xs text-ink-2">{connectionError ? "Could not check configuration." : !connections ? "Checking configuration…" : !connections.instagram.configured ? "Instagram credentials are missing." : !connections.storage.configured ? "Image storage is not configured." : "Instagram and image storage are configured. Test them in Settings to verify they work."} <Link to="/settings" className="text-moss underline">Open settings</Link></p>
          <label className={LABEL} htmlFor="when">Publish at ({tz})</label>
          <input id="when" type="datetime-local" className={INPUT} value={when} onChange={(e) => setWhen(e.target.value)} />
          <p className="text-xs text-ink-2">
            Recommended: {fmt(card.recommended_at, tz)}. A Story goes 15 minutes after its post.
          </p>
          <button type="button" disabled={busy} className={PRIMARY}
                  onClick={() => move("Scheduled", {
                    when: when ? fromLocalInput(when, tz).toISOString() : "",
                    ask: `Schedule this post for ${when ? fmt(fromLocalInput(when, tz).toISOString(), tz) : "its recommended time"}? `
                         + "It is approved in the queue and published then.",
                  })}>
            Schedule
          </button>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {col === "Inbox" && <button type="button" disabled={busy} className={PRIMARY} onClick={() => move("Planned", { note })}>Plan it</button>}
        {(["Planned", "Brief ready"].includes(col) || (col === "On hold" && !hasDrafts)) && (
          <button type="button" disabled={busy} className={col === "Brief ready" ? PRIMARY : SECONDARY}
                  onClick={() => move("Approved", { note, ask: "Approve this brief? The maker starts on it now." })}>
            Approve
          </button>
        )}
        {canAcceptDraft && (
          <button type="button" disabled={busy} className={PRIMARY}
                  onClick={() => move("Awaiting approval", { ask: "Approve the current draft as it is? Scheduling is a separate step." })}>
            Approve draft as is
          </button>
        )}
        {col === "Scheduled" && <button type="button" disabled={busy} className={SECONDARY} onClick={() => move("Awaiting approval")}>Unschedule</button>}
        {col === "On hold" && hasDrafts && (
          <button type="button" disabled={busy} className={SECONDARY} onClick={() => move("In review", { note })}>Send to the editor</button>
        )}
      </div>
      <div>
        <label className={LABEL} htmlFor="note">A note (for Rethink, Hold or Back to the partner)</label>
        <textarea id="note" rows={2} className={INPUT} value={note} onChange={(e) => setNote(e.target.value)}
                  placeholder="What to change, or why it waits" />
      </div>
      <div className="flex flex-wrap gap-2">
        {(PARTNER.includes(col) || (MADE.includes(col) && hasDrafts)) && (
          <button type="button" disabled={busy} className={SECONDARY}
                  onClick={() => needNote() && move(PARTNER.includes(col) ? "Planned" : "Making", { note })}>
            Rethink
          </button>
        )}
        {!["Live", "Measured", "Inbox", "Planned"].includes(col) && (
          <button type="button" disabled={busy} className={SECONDARY} onClick={() => move("Planned", { note })}>
            Back to the partner
          </button>
        )}
        {!["Live", "Measured", "On hold"].includes(col) && (
          <button type="button" disabled={busy} className={SECONDARY} onClick={() => move("On hold", { note })}>Hold</button>
        )}
        {!["Live", "Measured"].includes(col) && (
          <button type="button" disabled={busy} className={DANGER}
                  onClick={() => move("Closed", { note, ask: "Drop this card? Its drafts are rejected." })}>
            Drop
          </button>
        )}
      </div>
      <ErrorNote text={error} />
    </div>
  );
}

function Thread({ card, onDone }: { card: CardDetail; onDone: (c: CardDetail) => void }) {
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const wakes = PARTNER.includes(card.column);

  const send = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    api.comment(card.id, text).then((c) => { setText(""); onDone(c); })
      .catch((err) => setError(errorText(err))).finally(() => setBusy(false));
  };
  const attach = () => {
    const f = file.current?.files?.[0];
    if (!f) return;
    setBusy(true);
    api.attach(card.id, f, note).then((c) => { setNote(""); if (file.current) file.current.value = ""; onDone(c); })
      .catch((err) => setError(errorText(err))).finally(() => setBusy(false));
  };

  return (
    <Section title="Thread">
      <ol className="space-y-3">
        {card.thread.map((c) => (
          <li key={c.id} className={`rounded p-2 ${c.author === "user" ? "bg-moss-2" : c.author === "system" ? "bg-paper" : "bg-paper-2"}`}>
            <p className="mb-1 text-xs text-ink-3">{AUTHOR[c.author] ?? c.author} · {new Date(c.created_at).toLocaleString()}</p>
            <Text md={c.body_md} />
            {c.attachments.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {c.attachments.map((a, i) => a.url && (
                  <a key={i} href={a.url} target="_blank" rel="noreferrer" title={a.note ?? ""}>
                    <img src={a.url} alt={a.note ?? "attachment"} className="h-20 rounded border border-line object-cover" />
                  </a>
                ))}
              </div>
            )}
          </li>
        ))}
        {card.thread.length === 0 && <p className="text-sm text-ink-3">Nothing yet.</p>}
      </ol>
      <form onSubmit={send} className="mt-3 space-y-2">
        <textarea rows={3} className={INPUT} value={text} onChange={(e) => setText(e.target.value)}
                  placeholder={wakes ? "Answer the partner, or tell it what to change. It replies." : "A note on this card."} />
        <button type="submit" disabled={busy || !text.trim()} className={PRIMARY}>Comment</button>
      </form>
      <div className="mt-3 space-y-2 border-t border-line pt-3">
        <p className={LABEL}>Attach an image</p>
        <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" className="text-sm" />
        <input className={INPUT} value={note} onChange={(e) => setNote(e.target.value)}
               placeholder="Where it is from, and its credit (e.g. Photo: Jo Baker, mine to use)" />
        <button type="button" disabled={busy} className={SECONDARY} onClick={attach}>Attach</button>
        <p className="text-xs text-ink-3">The partner judges it and uses it only with a credit.</p>
      </div>
      <ErrorNote text={error} />
    </Section>
  );
}

export default function Card({ config }: { config: Config }) {
  const { id = "" } = useParams();
  const [card, setCard] = useState<CardDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tz = config.timezone;

  const load = useCallback(() => {
    api.card(id).then((c) => { setCard(c); setError(null); }).catch((e) => setError(errorText(e)));
  }, [id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!card?.busy) return;
    const t = window.setTimeout(load, POLL_MS);
    return () => window.clearTimeout(t);
  }, [card, load]);

  if (!card) return error ? <ErrorNote text={error} /> : <p className="text-sm text-ink-3">Loading…</p>;
  const latestRevision = [...card.thread].reverse().find((c) => c.author === "maker" && c.body_md.startsWith("Revised:"));
  const facts = card.facts as { title?: string; summary?: string; url?: string; figures?: Record<string, string>; body_md?: string } | null;

  return (
    <>
      <nav className="text-xs"><Link to="/" className="text-moss hover:underline">← Board</Link></nav>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{card.title}</h1>
        <span className="rounded bg-paper-2 px-2 py-0.5 text-xs font-medium text-ink-2">{card.closed ? "Closed" : card.column}</span>
        {card.busy && <span className="text-xs text-sun">{card.busy}…</span>}
      </div>
      <Link to={`/cards/${card.id}/preview`} className="mt-3 inline-block text-sm font-medium text-moss hover:underline">Preview Instagram post →</Link>
      {card.stuck_reason && card.column === "On hold" && (
        <p className="mt-2 rounded bg-clay-2 p-2 text-sm text-clay">On hold: {card.stuck_reason}</p>
      )}
      <div className="mt-3"><ErrorNote text={error} /></div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-4">
          <Section title="Brief">
            {card.brief_md ? <Text md={card.brief_md} /> : <p className="text-sm text-ink-3">No brief yet.</p>}
            {card.guidance && (
              <details className="mt-3">
                <summary className="cursor-pointer text-xs text-ink-2">Guidance for the maker</summary>
                <div className="mt-2"><Text md={card.guidance} /></div>
              </details>
            )}
            {card.plan.post && (
              <div className="mt-3">
                <p className={LABEL}>The partner's slide plan ({card.plan.post.kind})</p>
                <ol className="mt-1 list-decimal pl-5 text-sm">
                  {card.plan.post.slides.map((s, i) => <li key={i}><b>{s.type}</b>: {s.brief}</li>)}
                </ol>
              </div>
            )}
            {card.images.length > 0 && (
              <div className="mt-3">
                <p className={LABEL}>Images</p>
                <ul className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {card.images.map((img) => (
                    <li key={img.key} className={`rounded border p-1 text-xs ${img.used ? "border-moss" : "border-line opacity-70"}`}>
                      {img.url && <img src={img.url} alt={img.what ?? img.key} className="h-24 w-full rounded object-cover" />}
                      <p className="mt-1 font-medium">{img.key} · {img.used ? "used" : "not used"}</p>
                      <p className="text-ink-2">{img.credit ?? "no credit"}{img.licence ? ` · ${img.licence}` : " · no licence"}</p>
                      <p className="text-ink-3">{img.source}</p>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Section>

          {card.drafts.length > 0 && (
            <Section title={["Image updated. Review the current draft before scheduling.", "Draft updated. Review the changes before scheduling."].includes(card.stuck_reason || "") ? "Updated drafts · review changes" : "Drafts"}>
              {card.stuck_reason === "Draft updated. Review the changes before scheduling." && latestRevision &&
                <div className="mb-3 rounded bg-paper-2 p-3"><Text md={latestRevision.body_md} /><p className="mt-2 text-xs text-ink-2">Open a slide to view it and change its picture or words.</p></div>}
              <p className="mb-3 text-xs text-ink-2">To also post to X, open an Instagram draft and choose X when scheduling it.</p>
              <ul className="space-y-3">
                {card.drafts.map((d) => (
                  <li key={d.id}>
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <Link to={`/posts/${d.id}`} className="font-medium text-moss hover:underline">{d.kind}: {d.title}</Link>
                      <Badge status={d.status} />
                      {d.publish_at && <span className="text-xs text-ink-3">{fmt(d.publish_at, tz)}</span>}
                      {d.permalink && <a href={d.permalink} className="text-xs text-moss underline" target="_blank" rel="noreferrer">live</a>}
                    </div>
                    <div className="mt-1 flex gap-1 overflow-x-auto">
                      {d.slides.map((s, i) => s.url && (
                        <Link key={i} to={`/posts/${d.id}`}>
                          <img src={s.url} alt={s.alt_text} loading="lazy"
                               className={`h-28 shrink-0 rounded border border-line ${d.kind === "story" ? "aspect-[9/16]" : "aspect-[4/5]"}`} />
                        </Link>
                      ))}
                    </div>
                    {d.caption && <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-xs text-ink-2">{d.caption}</p>}
                    {d.error && <p className="mt-1 text-xs text-clay">{d.error}</p>}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {card.review && (
            <Section title={`The editor's verdict (round ${card.review.round})`}
                     aside={<span className={`text-xs font-semibold ${card.review.verdict === "pass" ? "text-moss" : "text-clay"}`}>{card.review.verdict}</span>}>
              {["Image updated. Review the current draft before scheduling.", "Draft updated. Review the changes before scheduling."].includes(card.stuck_reason || "") &&
                <p className="mb-2 text-xs text-ink-2">These notes are from before the image change. Review the current slides and alt text, or send the draft to the editor again.</p>}
              <Text md={card.review.summary} />
              <table className="mt-2 w-full text-xs">
                <tbody>
                  {card.review.checks.map((c) => (
                    <tr key={c.check} className="border-t border-line align-top">
                      <td className="py-1 pr-2 font-medium">{c.check}</td>
                      <td className={`py-1 pr-2 ${c.ok ? "text-moss" : "text-clay"}`}>{c.ok ? "pass" : "FAIL"}</td>
                      <td className="py-1 text-ink-2">{c.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {card.review.notes.length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-sm">
                  {card.review.notes.map((n, i) => (
                    <li key={i}><b>{n.target}{n.slide ? ` (slide ${n.slide})` : ""}</b>: {n.problem}{n.fix ? ` Suggested: ${n.fix}` : ""}</li>
                  ))}
                </ul>
              )}
            </Section>
          )}

          <Thread card={card} onDone={setCard} />
        </div>

        <aside className="space-y-4">
          <Section title="What next"><Actions card={card} config={config} onDone={setCard} /></Section>

          <Section title="Source">
            {facts ? (
              <div className="space-y-1 text-sm">
                <p className="font-medium">{facts.title}</p>
                {facts.summary && <p className="text-ink-2">{facts.summary}</p>}
                {facts.url && <a href={facts.url} target="_blank" rel="noreferrer" className="break-all text-xs text-moss underline">{facts.url}</a>}
                {facts.figures && Object.keys(facts.figures).length > 0 && (
                  <ul className="text-xs text-ink-2">
                    {Object.entries(facts.figures).map(([k, v]) => <li key={k}>{k}: <b>{v}</b></li>)}
                  </ul>
                )}
                {facts.body_md && (
                  <details>
                    <summary className="cursor-pointer text-xs text-ink-2">The text</summary>
                    <div className="mt-1 max-h-80 overflow-y-auto"><Text md={facts.body_md} /></div>
                  </details>
                )}
              </div>
            ) : (
              <Text md={card.body_md || "No source: the post is made from the card itself."} />
            )}
          </Section>

          <Section title="Spend" aside={<span className="font-mono text-xs">${card.spend_usd.toFixed(3)}</span>}>
            <ul className="space-y-1 text-xs text-ink-2">
              {card.runs.map((r) => (
                <li key={r.id} className="flex justify-between gap-2">
                  <span>{r.agent} · {r.status}{r.error ? ` · ${r.error}` : ""}</span>
                  <span className="font-mono">{r.cost_usd != null ? `$${r.cost_usd.toFixed(3)}` : "—"}</span>
                </li>
              ))}
              {card.runs.length === 0 && <li>No AI calls yet.</li>}
            </ul>
          </Section>

          {card.jobs.some((j) => j.status === "failed") && <Section title="Activity and recovery">
            <ul className="space-y-2 text-xs text-ink-2">{card.jobs.filter((j) => j.status === "failed").map((j) => <li key={j.id}><b>{j.kind}</b> failed after {j.attempts} attempt(s). {j.error || "Check the connection and settings."}</li>)}</ul>
            {((card.column === "Planned" || card.column === "Brief ready") || card.column === "Approved" || card.column === "In review") && <button type="button" className={`${SECONDARY} mt-3`} disabled={!!card.busy} onClick={() => { const agent = card.column === "Approved" ? "make" : card.column === "In review" ? "review" : "partner"; api.turn(card.id, agent).then(setCard).catch((e) => setError(errorText(e))); }}>Try this step again</button>}
            <p className="mt-2 text-xs text-ink-2">Check the error and fix the cause before retrying.</p>
          </Section>}

          <Section title="Moves">
            <ol className="space-y-1 text-xs">
              {card.events.map((e) => (
                <li key={e.id} className={e.refused ? "text-clay" : "text-ink-2"}>
                  {new Date(e.at).toLocaleString()} · {e.from_column ? `${e.from_column} → ` : ""}{e.to_column} · {e.actor}
                  {e.refused && " · REFUSED"}{e.reason ? ` · ${e.reason}` : ""}
                </li>
              ))}
            </ol>
          </Section>
        </aside>
      </div>
    </>
  );
}
