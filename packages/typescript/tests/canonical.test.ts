/**
 * Canonical-bytes parity tests.
 *
 * The fixture at tests/fixtures/canonical.json is emitted by the Python
 * implementation. If this test fails after a Python schema change, the
 * fixture must be regenerated (see Python's tests/scenarios for the
 * generator) and the TS canonical module updated in lockstep.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  canonicalBytes,
  canonicalJson,
  contentHash,
} from "../src/schema/canonical.js";

const fixturesDir = join(dirname(fileURLToPath(import.meta.url)), "fixtures");

const fixture = JSON.parse(
  readFileSync(join(fixturesDir, "canonical.json"), "utf-8"),
) as { canonical_json: string; content_hash: string };

const goldenPayload = () => ({
  span_id: "1111111111111111",
  trajectory_id: "22222222222222222222222222222222",
  name: "underwrite_application",
  type: "planner_step",
  start_time: new Date(Date.UTC(2026, 4, 17, 12, 0, 0, 123)), // millis=123
  end_time: new Date(Date.UTC(2026, 4, 17, 12, 0, 2, 0)),
  status: "ok",
  attributes: {
    kind: "planner_step",
    chosen_option: "deny",
    options_considered: ["approve", "deny", "manual_review"],
  },
  compliance: {
    policy_version_active: "lending-v2.3.1",
    agent_version: "loan-agent@0.1.0",
    regulatory_regime: ["ECOA", "FCRA"],
    subject_id: null,
  },
  inputs: { amount: 25000, dti: 0.46 },
  outputs: null,
});

describe("canonical bytes parity with Python", () => {
  it("produces byte-identical canonical JSON", () => {
    const js = canonicalJson(goldenPayload(), { excludeProvenance: false });
    expect(js).toBe(fixture.canonical_json);
  });

  it("produces matching SHA-256 content_hash", () => {
    // content_hash excludes provenance by default; our golden payload has no
    // provenance, so the two are equivalent.
    expect(contentHash(goldenPayload())).toBe(fixture.content_hash);
  });
});

describe("canonical bytes — local properties", () => {
  it("sorts keys lexicographically at every depth", () => {
    const out = canonicalJson({ z: 1, a: { y: 1, x: 2 }, m: 3 }, { excludeProvenance: false });
    expect(out).toBe('{"a":{"x":2,"y":1},"m":3,"z":1}');
  });

  it("emits minimal separators", () => {
    const out = canonicalJson({ a: 1 }, { excludeProvenance: false });
    expect(out).toBe('{"a":1}');
  });

  it("excludes provenance by default", () => {
    const out = canonicalJson({ span_id: "x", provenance: { content_hash: "f".repeat(64) } });
    expect(out).toBe('{"span_id":"x"}');
  });

  it("preserves null values", () => {
    const out = canonicalJson({ a: null, b: 1 }, { excludeProvenance: false });
    expect(out).toBe('{"a":null,"b":1}');
  });

  it("rejects NaN and Infinity", () => {
    expect(() =>
      canonicalBytes({ x: NaN }, { excludeProvenance: false }),
    ).toThrow(/NaN/);
    expect(() =>
      canonicalBytes({ x: Infinity }, { excludeProvenance: false }),
    ).toThrow(/Infinity/);
  });

  it("rejects ISO datetime strings that aren't in canonical form", () => {
    expect(() =>
      canonicalBytes({ t: "2026-05-17T12:00:00Z" }, { excludeProvenance: false }),
    ).toThrow(/canonical form/);
  });

  it("accepts ISO datetime strings in canonical form verbatim", () => {
    const out = canonicalJson(
      { t: "2026-05-17T12:00:00.000000Z" },
      { excludeProvenance: false },
    );
    expect(out).toBe('{"t":"2026-05-17T12:00:00.000000Z"}');
  });

  it("hash is stable across key-order permutations", () => {
    expect(contentHash({ a: 1, b: 2 })).toBe(contentHash({ b: 2, a: 1 }));
  });
});
