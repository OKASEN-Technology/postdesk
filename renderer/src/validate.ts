/** The slide spec checked against `schema/slide.json` (ajv). The same
 * file is shipped inside the Python package, so a spec is checked on both
 * sides of the wire. */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { Ajv2020 as Ajv, type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";

const HERE = dirname(fileURLToPath(import.meta.url));
export const SCHEMA_PATH = resolve(HERE, "..", "schema", "slide.json");

let compiled: ValidateFunction | null = null;

export function schema(): Record<string, unknown> {
  return JSON.parse(readFileSync(SCHEMA_PATH, "utf8")) as Record<string, unknown>;
}

function validator(): ValidateFunction {
  if (!compiled) {
    const ajv = new Ajv({ allErrors: true, strict: false, discriminator: true });
    compiled = ajv.compile(schema());
  }
  return compiled;
}

function describe(e: ErrorObject): string {
  const where = e.instancePath ? e.instancePath.replace(/^\//, "").replaceAll("/", ".") : "spec";
  if (e.keyword === "additionalProperties") return `${where}: unknown field ${JSON.stringify(e.params.additionalProperty)}`;
  if (e.keyword === "required") return `${where}: missing ${e.params.missingProperty}`;
  if (e.keyword === "enum") return `${where}: must be one of ${(e.params.allowedValues as unknown[]).join(", ")}`;
  return `${where}: ${e.message ?? e.keyword}`;
}

/** Problems with the spec; empty when it is valid. */
export function validateSpec(spec: unknown): string[] {
  const v = validator();
  if (v(spec)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const e of v.errors ?? []) {
    // The discriminator reports every branch's failures; keep the ones
    // about the branch the `type` picked, and the type error itself.
    const msg = describe(e);
    if (!seen.has(msg)) {
      seen.add(msg);
      out.push(msg);
    }
  }
  return out.slice(0, 8);
}
