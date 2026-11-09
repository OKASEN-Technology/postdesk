import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, errorText } from "../api";
import { CARD, ErrorNote, INPUT, PRIMARY } from "../ui";

/** The first visit sets the password; every later one asks for it. */
export default function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const [params] = useSearchParams();

  useEffect(() => {
    api.authStatus().then((s) => {
      setConfigured(s.configured);
      if (s.authenticated) navigate(params.get("next") || "/", { replace: true });
    });
  }, [navigate, params]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!configured && password !== again) {
      setError("The two passwords differ.");
      return;
    }
    setBusy(true);
    try {
      await (configured ? api.login(password) : api.setup(password));
      onSignedIn();
      navigate(params.get("next") || "/", { replace: true });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (configured === null) return <p className="p-8 text-sm text-ink-3">Loading…</p>;

  return (
    <main className="mx-auto mt-24 max-w-sm px-4">
      <h1 className="text-2xl font-semibold tracking-tight">postdesk</h1>
      <form onSubmit={submit} className={`${CARD} mt-4 space-y-3`}>
        {configured ? (
          <p className="text-sm text-ink-2">Sign in with the admin password.</p>
        ) : (
          <p className="text-sm text-ink-2">
            Welcome. Set a password for this page; you will need it every time you come back. It is stored here,
            hashed, and nowhere else.
          </p>
        )}
        <label className="block text-xs text-ink-2">
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={INPUT}
                 autoFocus minLength={configured ? 1 : 8} required />
        </label>
        {!configured && (
          <label className="block text-xs text-ink-2">
            Again
            <input type="password" value={again} onChange={(e) => setAgain(e.target.value)} className={INPUT} required />
          </label>
        )}
        <ErrorNote text={error} />
        <button type="submit" disabled={busy} className={PRIMARY}>{configured ? "Sign in" : "Set password"}</button>
      </form>
      {!configured && (
        <p className="mt-3 text-xs text-ink-3">
          Prefer a fixed one? Put <code>POSTDESK_ADMIN_PASSWORD</code> in <code>.env</code> and restart.
        </p>
      )}
    </main>
  );
}
