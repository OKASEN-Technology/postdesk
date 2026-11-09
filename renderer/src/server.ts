/**
 * The HTTP service (README "Running it"):
 *
 *   POST /render      a slide spec (JSON) in, a JPEG out. `?theme=` or the
 *                     spec's `theme` picks the theme; `?svg=1` returns SVG.
 *   GET  /health      {ok, version, themes}
 *   GET  /themes      every theme: palette, fonts, wordmark text
 *   GET  /schema      the slide JSON schema
 *
 * `RENDERER_TOKEN` set: every request but /health needs
 * `Authorization: Bearer <token>`. Unset: open, with a warning at start
 * (fine on localhost; never expose that to the internet).
 */

import { timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

import { render, RenderError } from "./render.js";
import { describeTheme, listThemes, ThemeError } from "./theme.js";
import { schema } from "./validate.js";

export const VERSION = "0.1.0";
const MAX_BODY = 1024 * 1024;

export interface ServerOptions {
  port?: number;
  host?: string;
  token?: string | null;
  log?: (line: string) => void;
}

function authorized(req: IncomingMessage, token: string | null | undefined): boolean {
  if (!token) return true;
  const [scheme, given = ""] = (req.headers.authorization ?? "").split(" ");
  const a = Buffer.from(given);
  const b = Buffer.from(token);
  return scheme?.toLowerCase() === "bearer" && a.length === b.length && timingSafeEqual(a, b);
}

function json(res: ServerResponse, status: number, body: unknown): void {
  const data = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "content-length": Buffer.byteLength(data) });
  res.end(data);
}

function readBody(req: IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on("data", (c: Buffer) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new RenderError("body too large", 413));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

export async function handle(req: IncomingMessage, res: ServerResponse, opts: ServerOptions): Promise<void> {
  const url = new URL(req.url ?? "/", "http://localhost");
  const path = url.pathname.replace(/\/+$/, "") || "/";
  try {
    if (req.method === "GET" && path === "/health") {
      return json(res, 200, { ok: true, version: VERSION, themes: listThemes() });
    }
    if (!authorized(req, opts.token)) return json(res, 401, { detail: "requires a valid bearer token (RENDERER_TOKEN)" });
    if (req.method === "GET" && path === "/themes") {
      return json(res, 200, listThemes().map((name) => describeTheme(name)));
    }
    if (req.method === "GET" && path === "/schema") return json(res, 200, schema());
    if (req.method === "POST" && path === "/render") {
      const body = await readBody(req);
      let spec: unknown;
      try {
        spec = JSON.parse(body.toString("utf8"));
      } catch {
        return json(res, 400, { detail: "body must be a JSON slide spec" });
      }
      const started = Date.now();
      const svg = url.searchParams.get("svg") === "1";
      const out = await render(spec, { theme: url.searchParams.get("theme") ?? undefined, svg });
      const type = (spec as { type?: string })?.type ?? "?";
      opts.log?.(`render ${type} ${out.length} bytes ${Date.now() - started}ms`);
      res.writeHead(200, {
        "content-type": svg ? "image/svg+xml" : "image/jpeg",
        "content-length": out.length,
        "cache-control": "no-store",
      });
      return void res.end(out);
    }
    return json(res, 404, { detail: `no route ${req.method} ${path}` });
  } catch (e) {
    if (e instanceof RenderError) return json(res, e.status, { detail: e.message });
    if (e instanceof ThemeError) return json(res, 400, { detail: e.message });
    opts.log?.(`error: ${(e as Error).stack ?? e}`);
    return json(res, 500, { detail: `render failed: ${(e as Error).message}` });
  }
}

export function createRendererServer(opts: ServerOptions = {}) {
  return createServer((req, res) => void handle(req, res, opts));
}

/** Start listening; resolves with the server once it is bound. */
export function serve(opts: ServerOptions = {}) {
  const port = opts.port ?? Number(process.env.PORT ?? 3100);
  const host = opts.host ?? process.env.HOST ?? "0.0.0.0";
  const token = opts.token === undefined ? process.env.RENDERER_TOKEN || null : opts.token;
  const log = opts.log ?? ((line: string) => console.log(line));
  const server = createRendererServer({ ...opts, token, log });
  return new Promise<typeof server>((resolve) => {
    server.listen(port, host, () => {
      log(`postdesk renderer ${VERSION} on http://${host}:${port} (themes: ${listThemes().join(", ") || "none"})`);
      if (!token) log("RENDERER_TOKEN is not set: /render is open. Fine on localhost; set it anywhere else.");
      resolve(server);
    });
  });
}
