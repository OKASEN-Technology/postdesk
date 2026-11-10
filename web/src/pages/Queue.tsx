import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, errorText, type Config, type Kind, type Row } from "../api";
import { fmt } from "../time";
import { Badge, ErrorNote } from "../ui";

const HOUR_MS = 60 * 60 * 1000;
const KINDS: Kind[] = ["image", "carousel", "story"];
const PAGE = 50;

/** The open tab's sections, in the order you work them. Rows arrive in
 * publish order, then newest first. A post publishing for over an hour is
 * stuck; one publishing now is about to go out, so it heads Scheduled. */
function sections(rows: Row[], now: number) {
  const stuck = (r: Row) => r.status === "publishing" && (!!r.error_excerpt || now - Date.parse(r.updated_at) > HOUR_MS);
  return [
    { title: "Needs attention", rows: [...rows.filter((r) => r.status === "failed"), ...rows.filter(stuck)] },
    { title: "To review", rows: rows.filter((r) => r.status === "draft") },
    { title: "Scheduled", rows: [...rows.filter((r) => r.status === "publishing" && !stuck(r)), ...rows.filter((r) => r.status === "approved")] },
  ];
}

function when(row: Row, tz: string) {
  switch (row.status) {
    case "draft": return row.publish_at ? `${fmt(row.publish_at, tz)} · suggested` : "—";
    case "approved": return fmt(row.publish_at, tz);
    case "publishing": return `since ${fmt(row.updated_at, tz)}`;
    case "published": return fmt(row.published_at, tz);
    default: return fmt(row.updated_at, tz);
  }
}

function Rows({ rows, tz }: { rows: Row[]; tz: string }) {
  return (
    <ul className="divide-y divide-line rounded-lg border border-line bg-white">
      {rows.map((r) => (
        <li key={r.id} className="relative flex gap-3 px-3 py-2 hover:bg-paper">
          <div className="flex w-10 shrink-0 justify-center">
            <div className={`h-12 overflow-hidden rounded border border-line bg-paper-2 ${r.kind === "story" ? "aspect-[9/16]" : "aspect-[4/5]"}`}>
              {r.first_slide_url && <img src={r.first_slide_url} alt="" loading="lazy" className="h-full w-full object-cover" />}
            </div>
          </div>
          <div className="min-w-0 flex-1 md:grid md:grid-cols-[minmax(0,1fr)_7rem_12rem_7rem] md:items-center md:gap-4">
            <div className="min-w-0">
              <Link to={`/posts/${r.id}`} className="block truncate text-sm font-medium text-ink after:absolute after:inset-0 hover:underline">
                {r.title}
              </Link>
              <p className="truncate text-xs text-ink-2">
                {r.channel === "x" ? "X" : "Instagram"} · {r.kind} · {r.slide_count} slide{r.slide_count === 1 ? "" : "s"}{r.source_ref ? ` · ${r.source_ref}` : ""}
              </p>
            </div>
            <Badge status={r.status} />
            <span className="text-xs text-ink-2">{when(r, tz)}</span>
            <span className="truncate text-xs text-ink-3">{r.created_by}</span>
          </div>
          {r.status === "failed" && r.error_excerpt && (
            <p className="absolute right-3 bottom-1 hidden max-w-xs truncate text-xs text-clay lg:block" title={r.error_excerpt}>
              {r.error_excerpt}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function Queue({ config }: { config: Config }) {
  const [params, setParams] = useSearchParams();
  const tab = params.get("status") === "done" ? "done" : "open";
  const page = Math.max(1, Number(params.get("page") ?? 1));
  const [open, setOpen] = useState<Row[] | null>(null);
  const [done, setDone] = useState<Row[] | null>(null);
  const [kind, setKind] = useState<Kind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const now = Date.now();
  const tz = config.timezone;

  useEffect(() => {
    api.listPosts("open").then(setOpen).catch((e) => setError(errorText(e)));
    api.listPosts("done", PAGE, (page - 1) * PAGE).then(setDone).catch((e) => setError(errorText(e)));
  }, [page]);

  const toReview = open?.filter((r) => r.status === "draft").length ?? 0;
  const scheduled = open?.filter((r) => r.status === "approved").length ?? 0;
  const attention = (open?.length ?? 0) - toReview - scheduled;
  const shown = kind ? (open ?? []).filter((r) => r.kind === kind) : open ?? [];

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Queue</h1>
      <p className="mt-1 max-w-3xl text-sm text-ink-2">
        Open a post to check its slides, caption and alt text, then approve it with a time. The publisher posts it
        shortly after that time, between {config.publisher_hours[0]} and {config.publisher_hours[1]} ({tz}).
        Nothing goes out without an approval here.
      </p>
      <nav className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <button type="button" onClick={() => setParams({})} className={tab === "open" ? "font-semibold text-ink" : "text-ink-3 hover:text-ink"}>
          To review &amp; scheduled{" "}
          <span className="font-mono text-xs font-normal">
            ({attention > 0 && `${attention} need attention · `}{toReview} to review · {scheduled} scheduled)
          </span>
        </button>
        <button type="button" onClick={() => setParams({ status: "done" })} className={tab === "done" ? "font-semibold text-ink" : "text-ink-3 hover:text-ink"}>
          Published &amp; rejected
        </button>
      </nav>
      <div className="mt-4"><ErrorNote text={error} /></div>

      {tab === "open" && open && (
        <div className="mt-4 space-y-4">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by kind">
            {[null, ...KINDS].map((k) => (
              <button key={k ?? "all"} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}
                      className={`rounded border px-2 py-0.5 text-xs font-medium ${kind === k ? "border-ink bg-ink text-paper" : "border-line bg-white text-ink hover:bg-paper-2"}`}>
                {k ?? "all"} · {k ? open.filter((r) => r.kind === k).length : open.length}
              </button>
            ))}
          </div>
          {open.length === 0 && (
            <p className="text-sm text-ink-2">
              Nothing waiting. Queue a post with <code>postdesk queue</code> or <Link to="/new" className="text-moss underline">the form</Link>.
            </p>
          )}
          {sections(shown, now).filter((s) => s.rows.length > 0).map((s) => (
            <section key={s.title}>
              <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-2">
                {s.title} <span className="font-mono font-normal text-ink-3">{s.rows.length}</span>
              </h2>
              <Rows rows={s.rows} tz={tz} />
            </section>
          ))}
        </div>
      )}

      {tab === "done" && done && (
        <div className="mt-4 space-y-3">
          {done.length === 0 ? <p className="text-sm text-ink-2">Nothing here yet.</p> : <Rows rows={done} tz={tz} />}
          <div className="flex gap-3 text-sm">
            {page > 1 && <button type="button" className="text-moss underline" onClick={() => setParams({ status: "done", page: String(page - 1) })}>← Newer</button>}
            {done.length === PAGE && <button type="button" className="text-moss underline" onClick={() => setParams({ status: "done", page: String(page + 1) })}>Older →</button>}
          </div>
        </div>
      )}
    </>
  );
}
