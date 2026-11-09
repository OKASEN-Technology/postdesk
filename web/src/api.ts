/** The API client. Every call carries the session cookie; a 401 sends the
 * app to the login page. */

export type Kind = "image" | "carousel" | "story" | "reel";
export type Status = "draft" | "approved" | "publishing" | "published" | "failed" | "rejected";

export interface Slide { asset_id: string; alt_text: string; url: string | null }

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
  updatePost: (id: string, body: { title?: string; caption?: string; alt_texts?: string[] }) =>
    call<Post>(`/api/posts/${id}`, json("PATCH", body)),
  approve: (id: string, publish_at: string) => call<Post>(`/api/posts/${id}/approve`, json("POST", { publish_at })),
  action: (id: string, what: "unapprove" | "reject" | "back-to-draft") => call<Post>(`/api/posts/${id}/${what}`, json("POST")),
  lint: (caption: string) => call<Lint>("/api/posts/lint-caption", json("POST", { caption })),
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
