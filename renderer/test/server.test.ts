import type { AddressInfo } from "node:net";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createRendererServer, VERSION } from "../src/server.js";
import { fixture, jpegSize } from "./helpers.js";

const TOKEN = "test-token";
let base = "";
const server = createRendererServer({ token: TOKEN, log: () => {} });

beforeAll(async () => {
  await new Promise<void>((ok) => server.listen(0, "127.0.0.1", ok));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterAll(() => new Promise<void>((ok) => server.close(() => ok())));

const auth = { authorization: `Bearer ${TOKEN}` };

describe("server", () => {
  it("answers /health without a token", async () => {
    const res = await fetch(`${base}/health`);
    expect(res.status).toBe(200);
    const body = (await res.json()) as { ok: boolean; version: string; themes: string[] };
    expect(body.ok).toBe(true);
    expect(body.version).toBe(VERSION);
    expect(body.themes).toContain("default");
  });

  it("refuses everything else without the token", async () => {
    expect((await fetch(`${base}/themes`)).status).toBe(401);
    const wrong = await fetch(`${base}/render`, {
      method: "POST",
      headers: { authorization: "Bearer nope", "content-type": "application/json" },
      body: JSON.stringify(fixture("cover.json")),
    });
    expect(wrong.status).toBe(401);
  });

  it("lists themes", async () => {
    const res = await fetch(`${base}/themes`, { headers: auth });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { name: string }[];
    expect(body.map((t) => t.name)).toContain("default");
  });

  it("serves the schema", async () => {
    const res = await fetch(`${base}/schema`, { headers: auth });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { $defs: Record<string, unknown> };
    expect(Object.keys(body.$defs)).toContain("story");
  });

  it("renders a slide", async () => {
    const res = await fetch(`${base}/render`, {
      method: "POST",
      headers: { ...auth, "content-type": "application/json" },
      body: JSON.stringify(fixture("story.json")),
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("image/jpeg");
    const buf = Buffer.from(await res.arrayBuffer());
    expect(jpegSize(buf)).toEqual({ width: 1080, height: 1920 });
  });

  it("returns 400 and the problems for a bad spec", async () => {
    const res = await fetch(`${base}/render`, {
      method: "POST",
      headers: { ...auth, "content-type": "application/json" },
      body: JSON.stringify({ type: "cover", kicker: "k" }),
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { detail: string };
    expect(body.detail).toMatch(/missing headline/);
  });

  it("returns 400 for a body that is not JSON", async () => {
    const res = await fetch(`${base}/render`, { method: "POST", headers: auth, body: "{nope" });
    expect(res.status).toBe(400);
  });

  it("404s elsewhere", async () => {
    expect((await fetch(`${base}/nope`, { headers: auth })).status).toBe(404);
  });
});
