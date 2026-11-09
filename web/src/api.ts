/** The API client. Every call carries the session cookie; a 401 sends the
 * app to the login page. */

export type Kind = "image" | "carousel" | "story" | "reel";
export type Status = "draft" | "approved" | "publishing" | "published" | "failed" | "rejected";

export interface Slide {
  asset_id: string; alt_text: string; url: string | null;
  /** A made slide's render spec and the words on it that can be changed (null for an uploaded slide). */
  spec: Record<string, unknown> | null; words: Record<string, string> | null;
}
export interface SlidePatch { index: number; fields: Record<string, string> }

export interface Post {
  id: string; channel: string; kind: Kind; status: Status; title: string; caption: string;
  slides: Slide[]; card_id: string | null; source_ref: string | null; publish_at: string | null;
  approved_by: string | null; approved_at: string | null; published_at: string | null;
  permalink: string | null; error: string | null; insights: Record<string, number> | null;
  insights_at: string | null; created_by: string; created_at: string; updated_at: string;
}

export interface Row {
  id: string; channel: string; kind: Kind; status: Status; title: string; first_slide_url: string | null;
  slide_count: number; card_id: string | null; source_ref: string | null; publish_at: string | null;
  published_at: string | null; error_excerpt: string | null; created_by: string; created_at: string;
  updated_at: string;
}

export interface Config {
  handle: string; timezone: string; feed_slot: string; story_slot: string; publisher_hours: [string, string];
  max_hashtags: number; public_base_url: string; storage: string; config_path: string | null;
}

export interface AuthStatus { configured: boolean; authenticated: boolean }
export interface Asset { id: string; url: string; public_url: string; width: number; height: number }
export interface Lint { errors: string[]; warnings: string[]; hashtags: number }

// ---------------------------------------------------------------- the board

export const COLUMNS = [
  "Inbox", "Planned", "Brief ready", "Approved", "Making", "In review", "Awaiting approval", "Scheduled", "Live",
  "Measured", "On hold",
] as const;
export type Column = (typeof COLUMNS)[number];
/** Only a person moves a card into these; the board asks before it does. */
export const GATES: Column[] = ["Approved", "Scheduled"];

export interface CardRow {
  id: string; kind: "post" | "story"; column: Column; title: string; source_ref: string | null;
  thumbnail_url: string | null; stuck_reason: string | null; verdict: string | null; busy: string | null;
  closed: boolean; created_by: string; created_at: string; updated_at: string;
}
export interface Attachment { asset_id: string | null; url: string | null; filename?: string | null; note?: string | null }
export interface Comment { id: string; author: string; body_md: string; attachments: Attachment[]; created_at: string }
export interface CardEvent {
  id: string; from_column: string | null; to_column: string; actor: string; reason: string | null; refused: boolean; at: string;
}
export interface Draft {
  id: string; kind: Kind; status: Status; title: string; caption: string; slides: Slide[]; publish_at: string | null;
  permalink: string | null; error: string | null;
}
export interface Run {
  id: string; agent: string; card_id: string | null; model: string; input_tokens: number; output_tokens: number;
  cost_usd: number | null; status: string; error: string | null; started_at: string;
}
export interface Job { id: string; kind: string; status: string; attempts: number; run_after: string; error: string | null }
export interface CardImage {
  key: string; for: string | null; url: string | null; asset_id: string | null; credit: string | null;
  licence: string | null; what: string | null; source: string; used: boolean; note?: string | null;
}
export interface Check { check: string; ok: boolean; note: string }
export interface Note { target: string; slide: number | null; problem: string; fix: string | null }
export interface Review {
  verdict: "pass" | "revise" | "hold"; summary: string; checks: Check[]; notes: Note[]; round: number; column: string; why: string;
}
export interface CardDetail extends CardRow {
  body_md: string; facts: Record<string, unknown> | null; brief_md: string | null; guidance: string | null;
  plan: { template?: string; post?: { kind: string; slides: { type: string; brief: string }[] } } & Record<string, unknown>;
  images: CardImage[]; review: Review | null; review_round: number; thread: Comment[]; events: CardEvent[];
  drafts: Draft[]; runs: Run[]; spend_usd: number; jobs: Job[]; recommended_at: string | null; gates: string[];
}
export interface Runs { runs: Run[]; month_usd: number; ceiling_usd: number }
export interface Source { key: string; type: string; options: Record<string, unknown> }
export interface Scan { filed: string[]; seen: number; deferred: number; errors: string[] }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  authStatus: () => call<AuthStatus>("/api/auth/status"),
  setup: (password: string) => call<AuthStatus>("/api/auth/setup", json("POST", { password })),
  login: (password: string) => call<AuthStatus>("/api/auth/login", json("POST", { password })),
  logout: () => call<AuthStatus>("/api/auth/logout", json("POST")),
  config: () => call<Config>("/api/config"),
  listPosts: (status: "open" | "done", limit?: number, offset = 0) =>
    call<Row[]>(`/api/posts?status=${status}${limit ? `&limit=${limit}&offset=${offset}` : ""}`),
  getPost: (id: string) => call<Post>(`/api/posts/${id}`),
  createPost: (body: { kind: Kind; title: string; caption: string; slides: { asset_id: string; alt_text: string }[]; source_ref?: string | null }) =>
    call<Post>("/api/posts", json("POST", body)),
  updatePost: (id: string, body: { title?: string; caption?: string; alt_texts?: string[]; slides?: SlidePatch[] }) =>
    call<Post>(`/api/posts/${id}`, json("PATCH", body)),
  approve: (id: string, publish_at: string) => call<Post>(`/api/posts/${id}/approve`, json("POST", { publish_at })),
  action: (id: string, what: "unapprove" | "reject" | "back-to-draft") => call<Post>(`/api/posts/${id}/${what}`, json("POST")),
  lint: (caption: string) => call<Lint>("/api/posts/lint-caption", json("POST", { caption })),
  cards: () => call<CardRow[]>("/api/cards"),
  card: (id: string) => call<CardDetail>(`/api/cards/${id}`),
  newCard: (body: { title: string; body_md: string; link?: string | null; kind?: "post" | "story"; column: "Inbox" | "Planned" }) =>
    call<CardDetail>("/api/cards", json("POST", body)),
  postAbout: (body: { title: string; text: string; link?: string | null; template?: string | null }) =>
    call<CardDetail>("/api/sources/manual", json("POST", body)),
  move: (id: string, column: string, note?: string | null, when?: string | null) =>
    call<CardDetail>(`/api/cards/${id}/move`, json("POST", { column, note: note || null, when: when || null })),
  comment: (id: string, body_md: string) => call<CardDetail>(`/api/cards/${id}/comments`, json("POST", { body_md })),
  attach: (id: string, file: File, note: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("note", note);
    return call<CardDetail>(`/api/cards/${id}/attachments`, { method: "POST", body: form });
  },
  turn: (id: string, agent: "partner" | "make" | "review") => call<CardDetail>(`/api/cards/${id}/${agent}`, json("POST")),
  runs: () => call<Runs>("/api/runs?limit=50"),
  sources: () => call<Source[]>("/api/sources"),
  scan: (key: string) => call<Scan>(`/api/sources/${encodeURIComponent(key)}/scan`, json("POST")),
  upload: (file: File, credit: string) => {
    const form = new FormData();
    form.append("file", file);
    if (credit) form.append("credit", credit);
    return call<Asset>("/api/assets", { method: "POST", body: form });
  },
};

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
