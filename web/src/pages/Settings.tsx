import { useEffect, useState, type ReactNode } from "react";
import {
  api, errorText, type Config, type Connections, type ModelChoice, type Permission, type PermissionIn, type Section, type SettingsData, type BrandLibrary,
} from "../api";
import { CARD, DANGER, ErrorNote, INPUT, LABEL, PRIMARY, SECONDARY } from "../ui";

/** The settings screen: Telegram pairing, the account, the schedule, the
 * voice, the sources, the images and their permissions, the models and the
 * spend, the notifiers. An edit is kept in the database and laid over
 * postdesk.toml at once; "Use the file's" takes it back. Secrets stay in
 * .env and are never shown here. */
export default function Settings({ onChange }: { config: Config; onChange?: () => void }) {
  const [data, setData] = useState<SettingsData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.settings().then(setData).catch((e) => setError(errorText(e))); }, []);

  if (!data) return error ? <ErrorNote text={error} /> : <p className="text-sm text-ink-3">Loading…</p>;

  const saved = (d: SettingsData) => { setData(d); onChange?.(); };
  const save = async (patch: Partial<Record<Section, unknown>>) => saved(await api.saveSettings(patch));
  const props = { data, save };

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
      <p className="mt-1 max-w-3xl text-sm text-ink-2">
        Changes here apply at once and are kept in the database, over{" "}
        {data.config_path ? <code className="text-xs">{data.config_path}</code> : "the defaults (no postdesk.toml found)"}.
        AI keys can be saved securely below; other service credentials are managed by the server administrator.
      </p>
      <ConnectionsPanel />
      <XConnection />
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <Telegram data={data} onChange={saved} />
          <Agents data={data} onChange={saved} />
          <Models {...props} />
          <Notifiers data={data} />
        </div>
        <div className="space-y-6">
          <Account {...props} />
          <Schedule {...props} />
          <Voice {...props} />
          <BrandLibraryPanel />
          <Images {...props} />
          <Sources {...props} />
        </div>
      </div>
      <Permissions />
    </>
  );
}


function ConnectionsPanel() {
  const [data, setData] = useState<Connections | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [verified, setVerified] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const load = () => api.connections().then(setData).catch((e) => setMessages((m) => ({ ...m, general: errorText(e) })));
  useEffect(() => { void load(); }, []);
  const test = async (name: string) => {
    setBusy(name);
    try { const r = await api.testConnection(name); setVerified((v) => ({ ...v, [name]: r.ok })); setMessages((m) => ({ ...m, [name]: r.detail })); }
    catch (e) { setVerified((v) => ({ ...v, [name]: false })); setMessages((m) => ({ ...m, [name]: errorText(e) })); }
    finally { setBusy(null); }
  };
  const saveKey = async (name: string) => {
    setBusy(name);
    try { await api.saveKey(name, key); setKey(""); setEditing(null); setVerified((v) => ({ ...v, [name]: true })); await load(); setMessages((m) => ({ ...m, [name]: "Key verified and saved securely." })); }
    catch (e) { setMessages((m) => ({ ...m, [name]: errorText(e) })); }
    finally { setBusy(null); }
  };
  if (!data) return <div className="mt-5"><ErrorNote text={messages.general} /></div>;
  const rows = [
    ["openai", "OpenAI", data.openai.configured, data.openai.selected ? "Used by selected models" : "No selected models"],
    ["anthropic", "Claude", data.anthropic.configured, data.anthropic.selected ? "Used by selected models" : "No selected models"],
    ["instagram", "Instagram", data.instagram.configured, "Required to publish"],
    ["telegram", "Telegram", data.telegram.configured, data.telegram.paired ? "Chat paired" : "Chat not paired"],
    ["storage", "Image storage", data.storage.configured, `${data.storage.kind} storage`],
    ["renderer", "Slide renderer", data.renderer.configured, "Creates post slides"],
  ] as const;
  return <section className={`${CARD} mt-6`}>
    <h2 className="text-base font-semibold">Connections and setup</h2>
    <p className="mt-1 text-sm text-ink-2">Configured means settings are present. Test each connection to verify it. Storage testing saves a small test image.</p>
    <ul className="mt-3 divide-y divide-line">
      {rows.map(([name, label, configured, description]) => <li key={name} className="py-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className="w-32 font-medium">{label}</span>
          <span className={configured ? "text-moss" : "text-clay"}>{configured ? verified[name] === true ? "Verified just now" : verified[name] === false ? "Test failed" : "Configured · not yet verified" : "Not configured"}</span>
          <span className="text-xs text-ink-2">{description}</span>
          <span className="ml-auto flex gap-3">
            {configured && <button type="button" disabled={busy !== null} className="text-moss hover:underline disabled:opacity-50" onClick={() => void test(name)}>{busy === name ? "Testing…" : "Test"}</button>}
            {(name === "openai" || name === "anthropic") && <button type="button" disabled={busy !== null || !data.credential_storage} className="text-moss hover:underline disabled:opacity-50" onClick={() => { setKey(""); setEditing(editing === name ? null : name); }}>Add or replace key</button>}
          </span>
        </div>
        {editing === name && <form className="mt-2 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void saveKey(name); }}>
          <input aria-label={`${label} API key`} type="password" autoComplete="off" className={`${INPUT} mt-0 max-w-md`} value={key} onChange={(e) => setKey(e.target.value)} placeholder="Paste API key" />
          <button className={PRIMARY} disabled={!key.trim() || busy !== null}>{busy === name ? "Verifying…" : "Verify and save"}</button>
          <button type="button" className={SECONDARY} onClick={() => { setKey(""); setEditing(null); }}>Cancel</button>
        </form>}
        {messages[name] && <p role="status" className="mt-1 text-xs text-ink-2">{messages[name]}</p>}
      </li>)}
    </ul>
    {!data.credential_storage && <p className="mt-2 text-xs text-ink-2">To manage AI keys here, the server administrator must configure credential encryption. Existing server keys continue to work.</p>}
    <p className="mt-2 text-xs text-ink-2">Instagram, Telegram and storage credentials are configured on the server. Pair Telegram below. Select your AI models and save them below.</p>
  </section>;
}

interface SectionProps { data: SettingsData; save: (patch: Partial<Record<Section, unknown>>) => Promise<void> }

/** A card with a title, its fields, and Save / Use the file's. */
function Panel({ title, section, data, save, patch, children, note }: {
  title: string; section?: Section; data?: SettingsData; save?: SectionProps["save"]; patch?: () => unknown;
  children: ReactNode; note?: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const overridden = section && data?.overridden[section];

  async function run(value: () => unknown) {
    if (!section || !save) return;
    setBusy(true); setError(null); setDone(false);
    try { await save({ [section]: value() }); setDone(true); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }

  return (
    <section className={CARD}>
      <div className="flex items-baseline gap-2">
        <h2 className="text-base font-semibold">{title}</h2>
        {overridden && <span className="rounded bg-sun-2 px-1.5 py-0.5 text-xs text-sun">changed here</span>}
      </div>
      {note && <p className="mt-1 text-sm text-ink-2">{note}</p>}
      <div className="mt-3 space-y-3">{children}</div>
      {patch && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button type="button" className={PRIMARY} disabled={busy} onClick={() => run(patch)}>Save</button>
          {overridden && (
            <button type="button" className={DANGER} disabled={busy} onClick={() => run(() => null)}>Use the file's</button>
          )}
          {done && <span className="text-xs text-moss">Saved.</span>}
        </div>
      )}
      <div className="mt-2"><ErrorNote text={error} /></div>
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className={LABEL}>{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-ink-3">{hint}</span>}
    </label>
  );
}

const list = (s: string) => s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

// ---------------------------------------------------------------- telegram

function Telegram({ data, onChange }: { data: SettingsData; onChange: (d: SettingsData) => void }) {
  const t = data.telegram;
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(f: () => Promise<unknown>, ok: string) {
    setBusy(true); setError(null); setMsg(null);
    try { await f(); onChange(await api.settings()); setMsg(ok); setCode(""); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }

  return (
    <section className={CARD}>
      <h2 className="text-base font-semibold">Telegram</h2>
      {!t.configured ? (
        <p className="mt-2 text-sm text-ink-2">
          No bot yet. Make one with @BotFather (<code>/newbot</code>), put its token in <code>.env</code> as{" "}
          <code>TELEGRAM_BOT_TOKEN</code> and restart postdesk. Everything works from this web UI without it.
        </p>
      ) : t.linked ? (
        <div className="mt-2 space-y-3 text-sm">
          <p>
            Paired with <b>{t.linked.title || t.linked.chat_id}</b>
            {t.linked.at ? ` since ${new Date(t.linked.at).toLocaleDateString()}` : ""}. Only that chat can act;
            any other is told the bot is private.
          </p>
          <p className="text-xs text-ink-3">
            Updates arrive by {t.mode === "webhook" ? "webhook (PUBLIC_BASE_URL is public https)" : "long polling (no public URL needed)"}.
          </p>
          <div className="flex flex-wrap gap-3">
            <button type="button" className={SECONDARY} disabled={busy} onClick={() => act(api.telegramTest, "Sent. Check the chat.")}>
              Send a test
            </button>
            <button type="button" className={DANGER} disabled={busy} onClick={() => act(api.telegramUnlink, "Unpaired.")}>
              Unpair
            </button>
          </div>
        </div>
      ) : (
        <form
          className="mt-2 space-y-3"
          onSubmit={(e) => { e.preventDefault(); if (code.trim()) act(() => api.telegramLink(code.trim()), "Paired."); }}
        >
          <p className="text-sm text-ink-2">Send <code>/start</code> to your bot; it answers with a six-letter code. Paste it here.</p>
          <div className="flex gap-2">
            <input className={`${INPUT} mt-0 max-w-[12rem] font-mono uppercase`} value={code} maxLength={12}
                   onChange={(e) => setCode(e.target.value)} placeholder="ABC123" aria-label="Pairing code" />
            <button type="submit" className={PRIMARY} disabled={busy || !code.trim()}>Pair</button>
          </div>
        </form>
      )}
      {msg && <p className="mt-2 text-xs text-moss">{msg}</p>}
      <div className="mt-2"><ErrorNote text={error} /></div>
    </section>
  );
}

// ---------------------------------------------------------------- agents and spend

function Agents({ data, onChange }: { data: SettingsData; onChange: (d: SettingsData) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const s = data.spend;
  const share = s.ceiling_usd > 0 ? Math.min(1, s.month_usd / s.ceiling_usd) : 1;

  async function toggle() {
    setBusy(true); setError(null);
    try { await (data.paused ? api.resume() : api.pause()); onChange(await api.settings()); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }

  return (
    <section className={CARD}>
      <h2 className="text-base font-semibold">Agents and spend</h2>
      <p className="mt-2 text-sm">
        ${s.month_usd.toFixed(2)} of ${s.ceiling_usd.toFixed(2)} this month.
        {s.over && <b className="text-clay"> The ceiling is reached: the agents have stopped.</b>}
      </p>
      <div className="mt-2 h-2 rounded bg-paper-2">
        <div className={`h-2 rounded ${s.over ? "bg-clay" : share > 0.8 ? "bg-sun" : "bg-moss"}`} style={{ width: `${share * 100}%` }} />
      </div>
      <p className="mt-3 text-sm text-ink-2">
        {data.paused
          ? `Paused by ${data.paused.by}: the scout and the agents wait. Scheduled posts still go out.`
          : "Running. Pausing stops the scout and the agents (not the publisher)."}
      </p>
      <button type="button" className={`${SECONDARY} mt-3`} disabled={busy} onClick={toggle}>
        {data.paused ? "Resume" : "Pause"}
      </button>
      <div className="mt-2"><ErrorNote text={error} /></div>
    </section>
  );
}

function ModelRow({ label, value, data, onChange }: {
  label: string; value: ModelChoice; data: SettingsData; onChange: (v: ModelChoice) => void;
}) {
  return (
    <div className="grid grid-cols-[6rem_1fr_7rem] items-center gap-2 text-sm">
      <span className="text-ink-2">{label}</span>
      <select className={`${INPUT} mt-0`} aria-label={`${label} model`} value={value.model} onChange={(e) => onChange({ ...value, model: e.target.value })}>
        {[...new Set([value.model, ...data.choices.models])].map((m) => <option key={m}>{m}</option>)}
      </select>
      <select className={`${INPUT} mt-0`} value={value.effort} onChange={(e) => onChange({ ...value, effort: e.target.value })}
              aria-label={`${label} effort`}>
        {data.choices.efforts.map((x) => <option key={x}>{x}</option>)}
      </select>
    </div>
  );
}

function Models({ data, save }: SectionProps) {
  const [llm, setLlm] = useState(data.values.llm);
  const set = (k: "partner" | "editor" | "maker" | "pick") => (v: ModelChoice) => setLlm({ ...llm, [k]: v });
  return (
    <Panel title="Models" section="llm" data={data} save={save} patch={() => llm}
           note="Choose an OpenAI or Claude model for each agent. Configure the matching API key under Connections. The monthly ceiling stops agents when reached.">
      <p className="text-xs text-ink-2">Partner develops ideas, Maker creates drafts, Editor reviews them, and Picker selects images. Changes take effect when you save.</p>
      <button type="button" className={SECONDARY} onClick={() => {
        const model = data.choices.models.find((m) => m.startsWith("gpt-"));
        if (model) setLlm({ ...llm, partner: { ...llm.partner, model }, editor: { ...llm.editor, model }, maker: { ...llm.maker, model }, pick: { ...llm.pick, model } });
      }}>Use OpenAI for all agents</button>
      <ModelRow label="Partner" value={llm.partner} data={data} onChange={set("partner")} />
      <ModelRow label="Editor" value={llm.editor} data={data} onChange={set("editor")} />
      <ModelRow label="Maker" value={llm.maker} data={data} onChange={set("maker")} />
      <ModelRow label="Picker" value={llm.pick} data={data} onChange={set("pick")} />
      <Field label="Monthly ceiling (USD)">
        <input type="number" min={0} step={1} className={INPUT} value={llm.monthly_usd}
               onChange={(e) => setLlm({ ...llm, monthly_usd: Number(e.target.value) })} />
      </Field>
    </Panel>
  );
}

// ---------------------------------------------------------------- notifiers

function Notifiers({ data }: { data: SettingsData }) {
  const [msg, setMsg] = useState<Record<string, string>>({});
  async function test(name: string) {
    try { await api.notifyTest(name); setMsg({ ...msg, [name]: "Sent." }); }
    catch (e) { setMsg({ ...msg, [name]: errorText(e) }); }
  }
  return (
    <section className={CARD}>
      <h2 className="text-base font-semibold">Notifiers</h2>
      <p className="mt-1 text-sm text-ink-2">
        One-way notices besides Telegram. Turn one on in postdesk.toml (<code>[notify.slack]</code>,{" "}
        <code>[notify.webhook]</code>, <code>[notify.email]</code>) and put its secret in <code>.env</code>.
      </p>
      <ul className="mt-3 divide-y divide-line text-sm">
        {data.notifiers.map((n) => (
          <li key={n.name} className="flex flex-wrap items-center gap-3 py-2">
            <span className="w-20 font-medium capitalize">{n.name}</span>
            <span className={n.ready ? "text-moss" : "text-ink-3"}>
              {n.ready ? "on" : n.on ? "on, but its secret is missing" : "off"}
            </span>
            {n.ready && n.events.length < 9 && <span className="text-xs text-ink-3">{n.events.join(", ")}</span>}
            {n.ready && (
              <button type="button" className="ml-auto text-xs text-ink-2 hover:underline" onClick={() => test(n.name)}>Send a test</button>
            )}
            {msg[n.name] && <span className="w-full text-xs text-ink-2">{msg[n.name]}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------- the account and the schedule

function Account({ data, save }: SectionProps) {
  const [a, setA] = useState(data.values.account);
  return (
    <Panel title="Account" section="account" data={data} save={save} patch={() => a}>
      <Field label="Instagram handle">
        <input className={INPUT} value={a.handle} onChange={(e) => setA({ ...a, handle: e.target.value.replace(/^@/, "") })} />
      </Field>
      <Field label="Website" hint="Its pages count as the account's own for figures and pictures.">
        <input className={INPUT} value={a.site ?? ""} onChange={(e) => setA({ ...a, site: e.target.value || null })} />
      </Field>
      <Field label="Timezone" hint="IANA name, e.g. Europe/London.">
        <input className={INPUT} value={a.timezone} onChange={(e) => setA({ ...a, timezone: e.target.value })} />
      </Field>
      <Field label="Audience" hint="Who the posts are for, in a sentence. The partner and the editor read it.">
        <textarea className={INPUT} rows={2} value={a.audience ?? ""} onChange={(e) => setA({ ...a, audience: e.target.value || null })} />
      </Field>
    </Panel>
  );
}

function Schedule({ data, save }: SectionProps) {
  const [s, setS] = useState(data.values.schedule);
  const num = (k: keyof typeof s) => (e: React.ChangeEvent<HTMLInputElement>) => setS({ ...s, [k]: Number(e.target.value) });
  return (
    <Panel title="Schedule" section="schedule" data={data} save={save} patch={() => s}
           note={`The daily slots, in the account's timezone. The digest goes at ${data.values.telegram.digest_at}.`}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Feed slot"><input className={INPUT} value={s.feed_slot} onChange={(e) => setS({ ...s, feed_slot: e.target.value })} /></Field>
        <Field label="Story slot"><input className={INPUT} value={s.story_slot} onChange={(e) => setS({ ...s, story_slot: e.target.value })} /></Field>
        <Field label="Feed posts a day"><input type="number" min={0} className={INPUT} value={s.max_feed_per_day} onChange={num("max_feed_per_day")} /></Field>
        <Field label="Stories a day"><input type="number" min={0} className={INPUT} value={s.max_story_per_day} onChange={num("max_story_per_day")} /></Field>
        <Field label="Least notice (hours)"><input type="number" min={0} className={INPUT} value={s.min_lead_hours} onChange={num("min_lead_hours")} /></Field>
        <Field label="Publisher hours">
          <div className="flex gap-1">
            <input className={INPUT} value={s.publisher_hours[0]} aria-label="from"
                   onChange={(e) => setS({ ...s, publisher_hours: [e.target.value, s.publisher_hours[1]] })} />
            <input className={INPUT} value={s.publisher_hours[1]} aria-label="to"
                   onChange={(e) => setS({ ...s, publisher_hours: [s.publisher_hours[0], e.target.value] })} />
          </div>
        </Field>
      </div>
    </Panel>
  );
}

function BrandLibraryPanel() {
  const [library, setLibrary] = useState<BrandLibrary | null>(null);
  const [form, setForm] = useState({account_id: "", bucket: "", prefix: "", public_url: "", access_key_id: "", secret_access_key: ""});
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [excluded, setExcluded] = useState<string[]>([]);
  const receive = (v: BrandLibrary) => { setLibrary(v); setExcluded(v.items.filter((i) => !i.enabled).map((i) => i.key)); };
  useEffect(() => { api.brandLibrary().then(receive).catch((e) => setMessage(errorText(e))); }, []);
  const visible = (library?.items ?? []).filter((i) => i.name.toLowerCase().includes(search.toLowerCase()));
  const update = (key: keyof typeof form, value: string) => setForm((v) => ({...v, [key]: value}));
  const connect = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setMessage("");
    try { receive(await api.saveBrandLibrary(form)); setForm({...form, access_key_id: "", secret_access_key: ""}); setEditing(false); setMessage("Connected. Media will be discovered automatically."); }
    catch (error) { setMessage(errorText(error)); } finally { setBusy(false); }
  };
  const saveSelection = async () => {
    setBusy(true); setMessage("");
    try { receive(await api.selectBrandLibrary(excluded)); setMessage("Media selection saved."); }
    catch (error) { setMessage(errorText(error)); } finally { setBusy(false); }
  };
  return <section className={`${CARD} space-y-3`}>
    <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-base font-semibold">Brand media library</h2>
      {library?.configured && <button className="text-xs text-moss hover:underline" onClick={() => { setForm({account_id: library.account_id ?? "", bucket: library.bucket ?? "", prefix: library.prefix ?? "", public_url: library.public_url ?? "", access_key_id: "", secret_access_key: ""}); setEditing(!editing); }}>Change connection</button>}</div>
    <p className="text-sm text-ink-2">Connect a public R2 folder. Postdesk finds new media automatically. Enabled JPEG, PNG and WebP images and sampled video frames may be shared with your AI provider when preparing ideas.</p>
    {(!library?.configured || editing) && <form className="space-y-2" onSubmit={connect}>
      {([ ["account_id", "Cloudflare account ID", "Account ID"], ["bucket", "Bucket name", "vigil-public-assets"], ["prefix", "Folder prefix (optional)", "vigil/"], ["public_url", "Public folder URL", "https://assets.example.com/vigil/"], ["access_key_id", "Read-only access key ID", "Access key ID"], ["secret_access_key", "Read-only secret access key", "Secret access key"] ] as const).map(([key, label, placeholder]) => <label key={key} className="block text-xs text-ink-2">{label}<input className={INPUT} value={form[key]} type={key === "secret_access_key" ? "password" : "text"} autoComplete="off" placeholder={placeholder} onChange={(e) => update(key, e.target.value)} required={key !== "prefix"} /></label>)}
      <p className="text-xs text-ink-2">Use an R2 Object Read only key scoped to this bucket. The credentials are encrypted and never displayed after saving.</p>
      <button className={PRIMARY} disabled={busy}>{busy ? "Connecting…" : "Connect library"}</button>
    </form>}
    {library?.configured && !editing && <>
      <p className="text-xs text-ink-2">{library.items.length} images and videos found · {library.bucket}/{library.prefix}</p>
      <input className={INPUT} value={search} placeholder="Find a file" aria-label="Find brand media" onChange={(e) => {setSearch(e.target.value); setPage(0);}} />
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">{visible.slice(page * 12, page * 12 + 12).map((item) => <label key={item.key} className="min-w-0 rounded-md border border-line p-2 text-xs">
        {item.type === "video" ? <video src={item.url} controls preload="none" className="aspect-square w-full bg-paper-2 object-contain" /> : <img src={item.url} alt="" loading="lazy" className="aspect-square w-full bg-paper-2 object-contain" />}
        <span className="mt-2 flex items-start gap-2"><input type="checkbox" checked={!excluded.includes(item.key)} onChange={(e) => setExcluded((old) => e.target.checked ? old.filter((x) => x !== item.key) : [...old, item.key])} /><span className="min-w-0 break-all">{item.name}</span></span>
      </label>)}</div>
      {!visible.length && <p className="text-xs text-ink-2">No matching media.</p>}
      <div className="flex flex-wrap items-center gap-3 text-xs"><button type="button" className={SECONDARY} disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page + 1} of {Math.max(1, Math.ceil(visible.length / 12))}</span><button type="button" className={SECONDARY} disabled={(page + 1) * 12 >= visible.length} onClick={() => setPage(page + 1)}>Next</button></div>
      <button type="button" className={PRIMARY} disabled={busy} onClick={() => void saveSelection()}>{busy ? "Saving…" : "Save media selection"}</button>
    </>}
    {message && <p role="status" className="text-xs text-ink-2">{message}</p>}
  </section>;
}

function Voice({ data, save }: SectionProps) {
  const v = data.values.voice;
  const [text, setText] = useState(v.text ?? "");
  const [tags, setTags] = useState(v.hashtags.join(" "));
  const [max, setMax] = useState(v.max_hashtags);
  const [retired, setRetired] = useState(v.retired_phrases.join("\n"));
  return (
    <Panel title="Voice" section="voice" data={data} save={save}
           patch={() => ({ text: text.trim() || null, hashtags: list(tags), max_hashtags: max, retired_phrases: lines(retired) })}
           note={v.from_file ? `Read from ${v.file}. Saving here keeps your text instead.` : "Kept here, over the voice file."}>
      <Field label="How the account writes">
        <textarea className={`${INPUT} font-mono text-xs`} rows={8} value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
      <Field label="Hashtags" hint="The pool the maker picks from.">
        <input className={INPUT} value={tags} onChange={(e) => setTags(e.target.value)} />
      </Field>
      <Field label="Hashtags a post">
        <input type="number" min={0} max={30} className={INPUT} value={max} onChange={(e) => setMax(Number(e.target.value))} />
      </Field>
      <Field label="Retired phrases" hint="One a line. The editor refuses a post that uses one.">
        <textarea className={INPUT} rows={3} value={retired} onChange={(e) => setRetired(e.target.value)} />
      </Field>
    </Panel>
  );
}

// ---------------------------------------------------------------- images and sources

const IMAGE_NOTES: Record<string, string> = {
  page: "the picture on the source's own page, credited to the site",
  wikimedia: "Wikimedia Commons: public domain, CC0 and CC BY only",
  business_discovery: "the subject's own Instagram account, through the API, with credit",
  attachment: "a picture you attach to a card, with where it is from",
};

function Images({ data, save }: SectionProps) {
  const [on, setOn] = useState<string[]>(data.values.images.sources);
  const flip = (k: string) => setOn(on.includes(k) ? on.filter((x) => x !== k) : [...on, k]);
  return (
    <Panel title="Images" section="images" data={data} save={save} patch={() => ({ sources: on })}
           note="Where a slide's picture may come from. Each one is stored with its credit, licence and where it was found.">
      {data.choices.image_sources.map((k) => (
        <label key={k} className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={on.includes(k)} onChange={() => flip(k)} />
          <span><b className="font-medium">{k}</b>{IMAGE_NOTES[k] ? `: ${IMAGE_NOTES[k]}` : ""}</span>
        </label>
      ))}
      <Field label="Theme" hint="The renderer's look for made slides.">
        <ThemeName data={data} save={save} />
      </Field>
    </Panel>
  );
}

function ThemeName({ data, save }: SectionProps) {
  const [name, setName] = useState(data.values.theme.name);
  return (
    <div className="flex gap-2">
      <input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
      <button type="button" className={`${SECONDARY} mt-1`} disabled={name === data.values.theme.name}
              onClick={() => save({ theme: { name } })}>Set</button>
    </div>
  );
}

function Sources({ data, save }: SectionProps) {
  const [text, setText] = useState(JSON.stringify(data.values.sources, null, 2));
  function parsed() {
    try { return JSON.parse(text); } catch { throw new Error("That is not valid JSON."); }
  }
  return (
    <Panel title="Sources" section="sources" data={data} save={save} patch={parsed}
           note={`What the scout reads, as in [sources] in postdesk.toml. Types: ${data.choices.source_types.join(", ")}.`}>
      <textarea className={`${INPUT} font-mono text-xs`} rows={Math.min(16, text.split("\n").length + 1)} value={text}
                onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label="Sources" />
    </Panel>
  );
}

// ---------------------------------------------------------------- permissions

const BLANK: PermissionIn = {
  holder: "", holder_kind: "person", instagram: null, domain: null, subject: null, scope: "", credit: "", tag: null,
  evidence: "", expires_on: null,
};

function Permissions() {
  const [rows, setRows] = useState<Permission[] | null>(null);
  const [form, setForm] = useState<PermissionIn>(BLANK);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.permissions().then(setRows).catch((e) => setError(errorText(e))); }, []);
  const set = (k: keyof PermissionIn) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value || (k === "holder_kind" ? "person" : null) });

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try { await api.addPermission(form); setForm(BLANK); setOpen(false); setRows(await api.permissions()); }
    catch (err) { setError(errorText(err)); }
  }
  async function revoke(id: string) {
    try { await api.revokePermission(id); setRows(await api.permissions()); } catch (err) { setError(errorText(err)); }
  }

  return (
    <section className={`${CARD} mt-6`}>
      <div className="flex items-baseline gap-3">
        <h2 className="text-base font-semibold">Image permissions</h2>
        <button type="button" className="ml-auto text-sm text-moss hover:underline" onClick={() => setOpen(!open)}>
          {open ? "Cancel" : "Record one"}
        </button>
      </div>
      <p className="mt-1 text-sm text-ink-2">
        A rights holder's yes, on file: their pictures (by Instagram handle or website) are then used with the credit they
        asked for. Only a person records one; keep the evidence (an email, a DM).
      </p>
      {open && (
        <form onSubmit={add} className="mt-4 grid gap-3 sm:grid-cols-2">
          <Field label="Holder"><input required className={INPUT} value={form.holder} onChange={set("holder")} /></Field>
          <Field label="Kind">
            <select className={INPUT} value={form.holder_kind} onChange={set("holder_kind")}>
              {["person", "organisation", "photographer", "other"].map((k) => <option key={k}>{k}</option>)}
            </select>
          </Field>
          <Field label="Instagram handle"><input className={INPUT} value={form.instagram ?? ""} onChange={set("instagram")} /></Field>
          <Field label="Website domain"><input className={INPUT} value={form.domain ?? ""} onChange={set("domain")} /></Field>
          <Field label="Only for (subject)" hint="Empty: everything they have."><input className={INPUT} value={form.subject ?? ""} onChange={set("subject")} /></Field>
          <Field label="Scope"><input required className={INPUT} value={form.scope} onChange={set("scope")} placeholder="feed posts and Stories" /></Field>
          <Field label="Credit"><input required className={INPUT} value={form.credit} onChange={set("credit")} placeholder="Photo: Jo Baker" /></Field>
          <Field label="Tag"><input className={INPUT} value={form.tag ?? ""} onChange={set("tag")} placeholder="@jo.bakes" /></Field>
          <Field label="Evidence"><input required className={INPUT} value={form.evidence} onChange={set("evidence")} placeholder="email of 2 March" /></Field>
          <Field label="Expires"><input type="date" className={INPUT} value={form.expires_on ?? ""} onChange={set("expires_on")} /></Field>
          <div className="sm:col-span-2"><button type="submit" className={PRIMARY}>Record</button></div>
        </form>
      )}
      <div className="mt-2"><ErrorNote text={error} /></div>
      {rows && rows.length === 0 && <p className="mt-3 text-sm text-ink-3">None on file.</p>}
      {rows && rows.length > 0 && (
        <ul className="mt-3 divide-y divide-line text-sm">
          {rows.map((p) => (
            <li key={p.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2">
              <b className="font-medium">{p.holder}</b>
              <span className="text-ink-2">{[p.instagram && `@${p.instagram}`, p.domain].filter(Boolean).join(" · ")}</span>
              {p.subject && <span className="text-ink-2">for {p.subject}</span>}
              <span className="text-ink-3">“{p.credit}”, {p.scope}; {p.evidence}</span>
              {!p.active && <span className="text-clay">expired</span>}
              <button type="button" className="ml-auto text-xs text-clay hover:underline" onClick={() => revoke(p.id)}>Revoke</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function XConnection() {
  const blank = {api_key: "", api_secret: "", access_token: "", access_secret: ""};
  const [values, setValues] = useState(blank);
  const [status, setStatus] = useState<{configured: boolean; credential_storage: boolean} | null>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const load = () => api.xConnection().then(setStatus).catch((e) => setMessage(errorText(e)));
  useEffect(() => { void load(); }, []);
  async function save() {
    setBusy(true); setMessage("");
    try { const result = await api.saveXConnection(values); setValues(blank); setEditing(false); setMessage(`Connected and verified @${result.account.username}.`); await load(); }
    catch (e) { setMessage(errorText(e)); } finally { setBusy(false); }
  }
  async function verify() {
    setBusy(true); setMessage("");
    try { const result = await api.verifyXConnection(); setMessage(`Verified @${result.account.username}.`); }
    catch (e) { setMessage(errorText(e)); } finally { setBusy(false); }
  }
  return <section className={`${CARD} mt-6`}>
    <h2 className="text-base font-semibold">X connection</h2>
    <p className="mt-1 text-sm text-ink-2">Connect your X developer app to choose X alongside Instagram for individual posts. X API usage may incur charges.</p>
    {status && <p className="mt-2 text-sm">{status.configured ? "Credentials saved" : "Not connected"}{!status.credential_storage && " · Credential encryption must be configured on the server."}</p>}
    <div className="mt-3 flex gap-2">
      <button type="button" className={SECONDARY} disabled={busy || !status?.credential_storage} onClick={() => { setEditing(!editing); setValues(blank); }}>{editing ? "Cancel" : status?.configured ? "Replace credentials" : "Connect X"}</button>
      {status?.configured && <button type="button" className={SECONDARY} disabled={busy} onClick={() => void verify()}>Verify account</button>}
    </div>
    {editing && <form className="mt-3 space-y-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      {([ ["api_key", "API key"], ["api_secret", "API key secret"], ["access_token", "Access token"], ["access_secret", "Access token secret"] ] as const).map(([key, label]) => <label className="block text-xs text-ink-2" key={key}>{label}<input type="password" autoComplete="off" required value={values[key]} className={INPUT} onChange={(e) => setValues({...values, [key]: e.target.value})} /></label>)}
      <button className={PRIMARY} disabled={busy}>{busy ? "Verifying…" : "Verify and save"}</button>
    </form>}
    {message && <p role="status" className="mt-2 text-sm text-ink-2">{message}</p>}
  </section>;
}
