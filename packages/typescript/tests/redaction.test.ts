/**
 * Redaction tests — strategies, patterns, filter integration with builder.
 */

import { describe, expect, it } from "vitest";

import type { SpanExporter } from "../src/exporter/base.js";
import { contentHash } from "../src/schema/canonical.js";
import { RedactionFilter } from "../src/redaction/filter.js";
import {
  ABA_ROUTING,
  DOB,
  MICR_LINE,
  US_BANK_ACCOUNT,
  US_SSN,
} from "../src/redaction/patternsFinance.js";
import { parsePolicy, passThroughPolicy } from "../src/redaction/policy.js";
import { FullRedaction, HmacFingerprint, PassThrough } from "../src/redaction/strategies.js";
import type { AgentCaptureSpan, ComplianceMetadata } from "../src/schema/span.js";
import { SpanBuilder } from "../src/span/builder.js";

const compliance: ComplianceMetadata = {
  policy_version_active: "v1",
  prompt_template_version: null,
  model_card_version: null,
  tool_schema_version: null,
  agent_version: "0.1.0",
  end_customer_id: "acme",
  subject_id: null,
  regulatory_regime: [],
  retention_class: "standard",
  data_classification: "internal",
};

function policyDoc() {
  return {
    version: "v1",
    default_strategy: "full",
    strategies: {
      full: { type: "full" },
      hmac: { type: "hmac", key: "test-key" },
    },
    field_rules: [
      { field_name: "ssn", strategy: "full" },
      { field_name: "account_number", strategy: "hmac" },
    ],
    pattern_rules: [
      { field_type: "ssn", strategy: "full" },
      { field_type: "routing_number", strategy: "hmac" },
    ],
  };
}

class Capture implements SpanExporter {
  spans: AgentCaptureSpan[] = [];
  export(s: AgentCaptureSpan): void {
    this.spans.push(s);
  }
  async shutdown(): Promise<void> {}
}

describe("strategies", () => {
  it("full redaction returns sentinel", () => {
    expect(new FullRedaction().redact("123-45-6789", { fieldType: "ssn" })).toBe("[REDACTED:ssn]");
  });

  it("pass-through returns verbatim", () => {
    expect(new PassThrough().redact("v", { fieldType: "x" })).toBe("v");
  });

  it("hmac is deterministic and field-typed", () => {
    const s = new HmacFingerprint({ key: "k", truncate: 16 });
    const a = s.redact("123-45-6789", { fieldType: "ssn" });
    const b = s.redact("123-45-6789", { fieldType: "ssn" });
    expect(a).toBe(b);
    expect(a.startsWith("[FP:")).toBe(true);
    expect(a.endsWith(":ssn]")).toBe(true);
  });

  it("hmac reads key from env", () => {
    process.env.TS_TEST_KEY = "envkey";
    try {
      const out = new HmacFingerprint({ keyEnv: "TS_TEST_KEY" }).redact("v", {
        fieldType: "x",
      });
      expect(out.startsWith("[FP:")).toBe(true);
    } finally {
      delete process.env.TS_TEST_KEY;
    }
  });

  it("hmac throws on missing env", () => {
    expect(() => new HmacFingerprint({ keyEnv: "MISSING_TS_KEY" }).redact("v", { fieldType: "x" })).toThrow();
  });
});

describe("finance recognizers", () => {
  it("SSN excludes invalid area numbers", () => {
    expect(US_SSN.findAll("000-12-3456")).toEqual([]);
    expect(US_SSN.findAll("987-65-4321")).toEqual([]); // 9xx
    expect(US_SSN.findAll("123-45-6789").length).toBe(1);
  });

  it("ABA routing validates checksum", () => {
    expect(ABA_ROUTING.findAll("ABA 011000015").length).toBe(1);
    expect(ABA_ROUTING.findAll("not-aba 011000016")).toEqual([]);
  });

  it("bank account requires context", () => {
    expect(US_BANK_ACCOUNT.findAll("hello 123456789012 world")).toEqual([]);
    expect(US_BANK_ACCOUNT.findAll("Account #: 123456789012").length).toBe(1);
  });

  it("DOB requires cue word", () => {
    expect(DOB.findAll("12/01/1985")).toEqual([]);
    expect(DOB.findAll("DOB: 12/01/1985").length).toBe(1);
  });

  it("MICR matches checksum pattern", () => {
    expect(MICR_LINE.findAll("A011000015A 123456789-").length).toBe(1);
  });
});

describe("policy parsing", () => {
  it("round-trips", () => {
    const p = parsePolicy(policyDoc());
    expect(p.version).toBe("v1");
    expect(p.strategyForField("ssn")?.name).toBe("full");
    expect(p.strategyForField("SSN")?.name).toBe("full"); // case-insensitive
    expect(p.strategyForField("nope")).toBeNull();
    expect(p.strategyForPattern("ssn").name).toBe("full");
    expect(p.strategyForPattern("routing_number").name).toBe("hmac");
    expect(p.strategyForPattern("unmapped").name).toBe("full"); // default
  });

  it("redaction floor: pass-through cannot ship recognized PII in cleartext", () => {
    const p = passThroughPolicy();
    for (const ft of ["ssn", "routing_number", "account_number", "micr", "date_of_birth"]) {
      const s = p.strategyForPattern(ft);
      expect(s.redact("123-45-6789", { fieldType: ft })).toBe(`[REDACTED:${ft}]`);
    }
    // The floor is scoped to recognized PII — other types still pass through.
    expect(p.strategyForPattern("public_marketing_id").redact("x", { fieldType: "public_marketing_id" })).toBe("x");
  });

  it("redaction floor: HMAC is still allowed for PII (floor forbids cleartext only)", () => {
    const p = parsePolicy({
      ...policyDoc(),
      pattern_rules: [{ field_type: "ssn", strategy: "hmac" }],
    });
    expect(p.strategyForPattern("ssn").name).toBe("hmac");
  });

  it("rejects malformed input", () => {
    expect(() => parsePolicy({} as never)).toThrow(/version/);
    expect(() => parsePolicy({ ...policyDoc(), strategies: { x: { type: "bogus" } } })).toThrow(/unknown type/);
    expect(() =>
      parsePolicy({
        ...policyDoc(),
        field_rules: [{ field_name: "x", strategy: "missing" }],
      }),
    ).toThrow(/undefined strategy/);
  });
});

describe("RedactionFilter end-to-end via builder", () => {
  it("redacts field-named values and pattern-matched free text", () => {
    const filter = new RedactionFilter({ policy: parsePolicy(policyDoc()) });
    const exp = new Capture();
    const builder = new SpanBuilder(exp, {
      defaultCompliance: compliance,
      redactionFilter: filter,
    });

    const open = builder.open({
      name: "tool",
      type: "tool_call",
      attributes: {
        kind: "tool_call",
        tool_name: "loan",
        tool_schema_version: null,
        arguments: { ssn: "111-22-3333", account_number: "12345678" },
        return_value: null,
      },
      inputs: { applicant: { ssn: "555-44-3333", notes: "ABA: 011000015" } },
    });
    builder.close(open, { outputs: { score: 700 } });
    expect(exp.spans.length).toBe(1);
    const span = exp.spans[0]!;
    const attrs = span.attributes as { arguments: Record<string, string> };
    expect(attrs.arguments.ssn).toBe("[REDACTED:ssn]");
    expect(attrs.arguments.account_number?.startsWith("[FP:")).toBe(true);
    const inputs = span.inputs as { applicant: { ssn: string; notes: string } };
    expect(inputs.applicant.ssn).toBe("[REDACTED:ssn]");
    expect(inputs.applicant.notes.includes("011000015")).toBe(false);
    expect(inputs.applicant.notes.includes("[FP:")).toBe(true);
    // content_hash covers post-redaction bytes.
    expect(span.provenance.content_hash).toBe(contentHash(span));
  });

  it("fingerprints compliance.subject_id (subject_ref floor)", () => {
    process.env.AGENT_CAPTURE_HMAC_KEY = "test-subject-key";
    const filter = new RedactionFilter({ policy: parsePolicy(policyDoc()) });
    const exp = new Capture();
    const builder = new SpanBuilder(exp, {
      defaultCompliance: { ...compliance, subject_id: "APP-10293" },
      redactionFilter: filter,
    });
    const open = builder.open({
      name: "tool",
      type: "tool_call",
      attributes: { kind: "tool_call", tool_name: "x", tool_schema_version: null, arguments: {}, return_value: null },
    });
    builder.close(open, {});
    const sid = exp.spans[0]!.compliance.subject_id ?? "";
    expect(sid.startsWith("[FP:")).toBe(true);
    expect(sid.endsWith(":subject_id]")).toBe(true);
    expect(sid.includes("APP-10293")).toBe(false);
    expect(exp.spans[0]!.provenance.content_hash).toBe(contentHash(exp.spans[0]!));
  });
});
