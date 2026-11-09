import { useCallback, useEffect, useState, type DragEvent, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, COLUMNS, errorText, GATES, type CardRow, type Column, type Config, type Runs, type Source } from "../api";
import { ErrorNote, INPUT, LABEL, PRIMARY, SECONDARY } from "../ui";

const POLL_MS = 5000;
/** What a drop on each column asks for. Columns the agents or the publisher set take no drops. */
const DROPPABLE: Column[] = ["Inbox", "Planned", "Approved", "In review", "Awaiting approval", "Scheduled", "On hold"];

const GATE_QUESTION: Record<string, string> = {
  Approved: "Approve this brief? The maker starts on it now.",
  Scheduled: "Schedule this post at its recommended time? It is approved in the queue and goes out then.",
};

function Tile({ card, onDragStart }: { card: CardRow; onDragStart: (e: DragEvent, card: CardRow) => void }) {
  return (
    <li draggable onDragStart={(e) => onDragStart(e, card)}
        className="relative rounded-md border border-line bg-white p-2 shadow-sm hover:border-ink-3">
      <div className="flex gap-2">
        {card.thumbnail_url ? (
          <img src={card.thumbnail_url} alt="" loading="lazy"
               className={`h-14 shrink-0 rounded border border-line object-cover ${card.kind === "story" ? "aspect-[9/16]" : "aspect-[4/5]"}`} />
        ) : (
          <div className="h-14 w-11 shrink-0 rounded border border-dashed border-line bg-paper" />
        )}
        <div className="min-w-0">
          <Link to={`/cards/${card.id}`} className="line-clamp-3 text-sm font-medium leading-snug text-ink after:absolute after:inset-0 hover:underline">
            {card.title}
          </Link>
          <p className="mt-0.5 text-xs text-ink-3">{card.kind} · {card.column}</p>
        </div>
      </div>
      {card.busy && <p className="mt-1 text-xs text-sun">{card.busy}…</p>}
      {card.stuck_reason && card.column === "On hold" && (
        <p className="mt-1 line-clamp-2 text-xs text-clay" title={card.stuck_reason}>{card.stuck_reason}</p>
      )}
    </li>
  );
}

function NewCard({ onFiled }: { onFiled: () => void }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [link, setLink] = useState("");
  const [start, setStart] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const req = start
      ? api.postAbout({ title, text, link: link || null })
      : api.newCard({ title, body_md: text, link: link || null, column: "Inbox" });
    req.then(() => { setTitle(""); setText(""); setLink(""); onFiled(); })
      .catch((err) => setError(errorText(err)))
      .finally(() => setBusy(false));
  };

  return (
    <form onSubmit={submit} className="space-y-2 rounded-lg border border-line bg-white p-3">
      <p className={LABEL}>Post about this</p>
      <input className={INPUT} placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <textarea className={INPUT} rows={3} placeholder="The text to post from (every figure in the post comes from here)"
                value={text} onChange={(e) => setText(e.target.value)} />
      <input className={INPUT} placeholder="…or just a link (the page is fetched)" value={link} onChange={(e) => setLink(e.target.value)} />
      <label className="flex items-center gap-2 text-xs text-ink-2">
        <input type="checkbox" checked={start} onChange={(e) => setStart(e.target.checked)} />
        Start the partner on it now (otherwise it waits in the Inbox)
      </label>
      <ErrorNote text={error} />
      <button type="submit" disabled={busy || (!title.trim() && !link.trim())} className={PRIMARY}>
        {busy ? "Filing…" : "File it"}
      </button>
    </form>
  );
}

function Sources({ onScanned }: { onScanned: () => void }) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { api.sources().then(setSources).catch(() => setSources([])); }, []);
  if (!sources) return null;
  return (
    <div className="space-y-2 rounded-lg border border-line bg-white p-3">
      <p className={LABEL}>Sources</p>
      {sources.length === 0 ? (
        <p className="text-xs text-ink-2">None in postdesk.toml. Add a feed under <code>[sources]</code> and the scout files its new items here.</p>
      ) : (
        <ul className="space-y-1">
          {sources.map((s) => (
            <li key={s.key} className="flex items-center justify-between gap-2 text-sm">
              <span className="truncate">{s.key} <span className="text-xs text-ink-3">({s.type})</span></span>
              <button type="button" disabled={busy !== null} className="text-xs text-moss hover:underline disabled:opacity-50"
                      onClick={() => {
                        setBusy(s.key);
                        api.scan(s.key)
                          .then((r) => {
                            setNote(`${s.key}: ${r.filed.length} filed, ${r.seen} seen` + (r.deferred ? `, ${r.deferred} next run` : "")
                                    + (r.errors.length ? `. ${r.errors.join("; ")}` : ""));
                            onScanned();
                          })
                          .catch((e) => setNote(errorText(e)))
                          .finally(() => setBusy(null));
                      }}>
                {busy === s.key ? "Scanning…" : "Scan now"}
              </button>
            </li>
          ))}
        </ul>
      )}
      {note && <p className="text-xs text-ink-2">{note}</p>}
    </div>
  );
}

export default function Board({ config }: { config: Config }) {
  const [cards, setCards] = useState<CardRow[] | null>(null);
  const [runs, setRuns] = useState<Runs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState<Column | null>(null);

  const load = useCallback(() => {
    api.cards().then((c) => { setCards(c); setError(null); }).catch((e) => setError(errorText(e)));
    api.runs().then(setRuns).catch(() => undefined);
  }, []);

  useEffect(() => {
    load();
    const t = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(t);
  }, [load]);

  const onDragStart = (e: DragEvent, card: CardRow) => {
    e.dataTransfer.setData("text/plain", card.id);
    e.dataTransfer.effectAllowed = "move";
  };

  const onDrop = (e: DragEvent, column: Column) => {
    e.preventDefault();
    setOver(null);
    const id = e.dataTransfer.getData("text/plain");
    const card = cards?.find((c) => c.id === id);
    if (!card || card.column === column) return;
    if (GATES.includes(column) && !window.confirm(GATE_QUESTION[column])) return;
    let note: string | null = null;
    if (column === "On hold") note = window.prompt("Why is it on hold?") ?? null;
    api.move(id, column, note).then(load).catch((err) => setError(errorText(err)));
  };

  const shown = COLUMNS.filter((c) => c !== "Measured" || cards?.some((x) => x.column === c));

  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Board</h1>
        {runs && (
          <span className="text-xs text-ink-3">
            Claude this month: ${runs.month_usd.toFixed(2)} of ${runs.ceiling_usd.toFixed(2)}
          </span>
        )}
      </div>
      <p className="mt-1 max-w-3xl text-sm text-ink-2">
        Each card is one post. The partner writes the brief, you approve it, the maker makes it, the editor reads it,
        and you schedule it. Only you move a card into <b>Approved</b> or <b>Scheduled</b>; nothing is published
        without that. Times are in {config.timezone}.
      </p>
      <div className="mt-3"><ErrorNote text={error} /></div>

      <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-start">
        <div className="flex min-w-0 flex-1 gap-3 overflow-x-auto pb-4">
          {shown.map((col) => {
            const here = (cards ?? []).filter((c) => c.column === col);
            const droppable = DROPPABLE.includes(col);
            return (
              <section key={col}
                       onDragOver={(e) => { if (droppable) { e.preventDefault(); setOver(col); } }}
                       onDragLeave={() => setOver((o) => (o === col ? null : o))}
                       onDrop={(e) => droppable && onDrop(e, col)}
                       className={`flex w-60 shrink-0 flex-col rounded-lg p-2 ${over === col ? "bg-moss-2" : "bg-paper-2"}`}>
                <h2 className="mb-2 flex items-center justify-between px-1 text-xs font-semibold uppercase tracking-wide text-ink-2">
                  <span>{col}{GATES.includes(col) && <span className="ml-1 text-moss" title="Only you move a card here">●</span>}</span>
                  <span className="font-mono font-normal text-ink-3">{here.length}</span>
                </h2>
                <ul className="space-y-2">
                  {here.map((c) => <Tile key={c.id} card={c} onDragStart={onDragStart} />)}
                </ul>
                {cards && here.length === 0 && <p className="px-1 text-xs text-ink-3">—</p>}
              </section>
            );
          })}
        </div>
        <aside className="w-full shrink-0 space-y-3 lg:w-72">
          <NewCard onFiled={load} />
          <Sources onScanned={load} />
          <p className="text-xs text-ink-3">
            Made a post by hand? It is in the <Link to="/queue" className="text-moss underline">queue</Link>, and the
            board files it for the editor within a minute.
          </p>
          <button type="button" className={SECONDARY} onClick={load}>Refresh</button>
        </aside>
      </div>
    </>
  );
}
