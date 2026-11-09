import { describe, expect, it } from "vitest";

import { validateSpec } from "../src/validate.js";
import { fixture, fixtureNames } from "./helpers.js";

describe("validateSpec", () => {
  it.each(fixtureNames())("accepts %s", (name) => {
    expect(validateSpec(fixture(name))).toEqual([]);
  });

  it("names the missing field", () => {
    expect(validateSpec({ type: "cover", kicker: "k", headline: "h" })).toEqual(["spec: missing sub"]);
  });

  it("refuses unknown fields", () => {
    const spec = { ...fixture("cta.json"), extra: 1 };
    expect(validateSpec(spec)).toEqual(['spec: unknown field "extra"']);
  });

  it("refuses an unknown type", () => {
    expect(validateSpec({ type: "poster" })[0]).toMatch(/type/);
  });

  it("refuses a non-object", () => {
    expect(validateSpec("cover")).not.toEqual([]);
    expect(validateSpec(null)).not.toEqual([]);
  });

  it("caps text fields", () => {
    const spec = { ...fixture("statement.json"), body: "x".repeat(601) };
    expect(validateSpec(spec)[0]).toMatch(/body/);
  });

  it("only takes http(s) and data image urls", () => {
    const spec = { ...fixture("caption.json"), image: { url: "file:///etc/passwd" } };
    expect(validateSpec(spec)[0]).toMatch(/image\.url/);
  });
});
