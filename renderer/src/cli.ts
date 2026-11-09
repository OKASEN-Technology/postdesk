#!/usr/bin/env node
/**
 *   postdesk-renderer                              # serve on $PORT (3100)
 *   postdesk-renderer serve [--port N] [--token T]
 *   postdesk-renderer render --spec slide.json --out slide.jpg [--theme name] [--svg]
 *   postdesk-renderer themes
 */

import { readFileSync, writeFileSync } from "node:fs";

import { render } from "./render.js";
import { serve, VERSION } from "./server.js";
import { listThemes } from "./theme.js";

function arg(argv: string[], name: string): string | undefined {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : undefined;
}

async function main(argv: string[]): Promise<number> {
  const [cmd = "serve", ...rest] = argv;
  if (cmd === "--help" || cmd === "-h") {
    console.log("postdesk-renderer [serve|render|themes]\n  render --spec slide.json --out slide.jpg [--theme name] [--svg]");
    return 0;
  }
  if (cmd === "--version" || cmd === "-v") {
    console.log(VERSION);
    return 0;
  }
  if (cmd === "themes") {
    for (const t of listThemes()) console.log(t);
    return 0;
  }
  if (cmd === "render") {
    const specPath = arg(rest, "--spec");
    const out = arg(rest, "--out");
    if (!specPath || !out) {
      console.error("usage: postdesk-renderer render --spec slide.json --out slide.jpg [--theme name] [--svg]");
      return 2;
    }
    const spec = JSON.parse(readFileSync(specPath, "utf8"));
    const data = await render(spec, { theme: arg(rest, "--theme"), svg: rest.includes("--svg") });
    writeFileSync(out, data);
    console.log(`${out}: ${data.length} bytes`);
    return 0;
  }
  if (cmd === "serve") {
    const port = arg(rest, "--port");
    const token = arg(rest, "--token");
    await serve({ port: port ? Number(port) : undefined, token: token ?? undefined });
    return -1; // keep running
  }
  console.error(`unknown command ${cmd}`);
  return 2;
}

main(process.argv.slice(2)).then(
  (code) => {
    if (code >= 0) process.exit(code);
  },
  (e) => {
    console.error(`error: ${(e as Error).message}`);
    process.exit(1);
  },
);
