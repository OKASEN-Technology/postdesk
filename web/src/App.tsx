import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, type Config } from "./api";
import Login from "./pages/Login";
import Queue from "./pages/Queue";
import PostEditor from "./pages/PostEditor";
import NewPost from "./pages/NewPost";

export interface Shell { config: Config }

function Header({ config }: { config: Config | null }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const tab = (to: string, label: string, active: boolean) => (
    <Link to={to} className={`rounded px-2 py-1 text-sm ${active ? "bg-paper-2 font-medium text-ink" : "text-ink-2 hover:text-ink"}`}>
      {label}
    </Link>
  );
  return (
    <header className="border-b border-line bg-paper">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-3">
        <Link to="/" className="mr-2 text-lg font-semibold tracking-tight text-ink">postdesk</Link>
        {tab("/", "Queue", pathname === "/" || pathname.startsWith("/posts"))}
        {tab("/new", "New post", pathname === "/new")}
        <span className="ml-auto text-xs text-ink-3">
          {config?.handle ? `@${config.handle} · ` : ""}{config?.timezone ?? ""}
        </span>
        <button
          type="button"
          onClick={() => api.logout().then(() => navigate("/login"))}
          className="text-xs text-ink-2 hover:text-ink hover:underline"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [authed, setAuthed] = useState<boolean | null>(null);
  const location = useLocation();

  useEffect(() => {
    let live = true;
    api.authStatus().then((s) => {
      if (!live) return;
      setAuthed(s.authenticated);
      if (s.authenticated) api.config().then((c) => live && setConfig(c)).catch(() => undefined);
    });
    return () => { live = false; };
  }, [location.pathname]);

  if (location.pathname === "/login") return <Login onSignedIn={() => setAuthed(true)} />;
  if (authed === null) return <p className="p-8 text-sm text-ink-3">Loading…</p>;
  if (!authed) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;

  return (
    <div className="min-h-screen">
      <Header config={config} />
      <main className="mx-auto max-w-6xl px-4 py-6">
        {config ? (
          <Routes>
            <Route path="/" element={<Queue config={config} />} />
            <Route path="/new" element={<NewPost config={config} />} />
            <Route path="/posts/:id" element={<PostEditor config={config} />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        ) : (
          <p className="text-sm text-ink-3">Loading…</p>
        )}
      </main>
    </div>
  );
}
