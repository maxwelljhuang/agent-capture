/**
 * traced() / tracedScope() wrapper tests.
 */

import { describe, expect, it } from "vitest";

import type { SpanExporter } from "../src/exporter/base.js";
import { traced, tracedScope } from "../src/instrumentation/traced.js";
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

class Capture implements SpanExporter {
  spans: AgentCaptureSpan[] = [];
  export(s: AgentCaptureSpan): void {
    this.spans.push(s);
  }
  async shutdown(): Promise<void> {}
}

describe("traced()", () => {
  it("wraps a sync function and emits one span", () => {
    const exp = new Capture();
    const builder = new SpanBuilder(exp, { defaultCompliance: compliance });
    const wrapped = traced(
      {
        type: "retrieval",
        name: "fetch",
        attributes: {
          kind: "retrieval",
          source_identifier: "x",
          query: null,
          returned_document_ids: [],
          relevance_scores: [],
        },
        builder,
      },
      (applicantId: string) => ({ score: 700, applicantId }),
    );
    const result = wrapped("abc");
    expect(result).toEqual({ score: 700, applicantId: "abc" });
    expect(exp.spans.length).toBe(1);
    expect(exp.spans[0]!.name).toBe("fetch");
    expect(exp.spans[0]!.status).toBe("ok");
  });

  it("captures async function exceptions and re-raises", async () => {
    const exp = new Capture();
    const builder = new SpanBuilder(exp, { defaultCompliance: compliance });
    const wrapped = traced(
      {
        type: "retrieval",
        builder,
        attributes: {
          kind: "retrieval",
          source_identifier: "x",
          query: null,
          returned_document_ids: [],
          relevance_scores: [],
        },
      },
      async (): Promise<never> => {
        throw new Error("boom");
      },
    );
    await expect(wrapped()).rejects.toThrow("boom");
    expect(exp.spans.length).toBe(1);
    expect(exp.spans[0]!.status).toBe("error");
    expect(exp.spans[0]!.error?.message).toBe("boom");
  });
});

describe("tracedScope()", () => {
  it("emits one span and returns body's value", async () => {
    const exp = new Capture();
    const builder = new SpanBuilder(exp, { defaultCompliance: compliance });
    const out = await tracedScope(
      {
        type: "planner_step",
        name: "decide",
        builder,
        attributes: {
          kind: "planner_step",
          decision_rationale: null,
          options_considered: [],
          chosen_option: null,
        },
      },
      async () => 42,
    );
    expect(out).toBe(42);
    expect(exp.spans.length).toBe(1);
    expect(exp.spans[0]!.name).toBe("decide");
  });

  it("nested traced functions chain via the AsyncLocalStorage parent", () => {
    const exp = new Capture();
    const builder = new SpanBuilder(exp, { defaultCompliance: compliance });
    const inner = traced(
      {
        type: "retrieval",
        name: "inner",
        attributes: {
          kind: "retrieval",
          source_identifier: "x",
          query: null,
          returned_document_ids: [],
          relevance_scores: [],
        },
        builder,
      },
      () => "data",
    );
    const outer = traced(
      {
        type: "planner_step",
        name: "outer",
        attributes: {
          kind: "planner_step",
          decision_rationale: null,
          options_considered: [],
          chosen_option: null,
        },
        builder,
      },
      () => inner(),
    );
    outer();
    // Leaves-first: inner ships before outer.
    expect(exp.spans.map((s) => s.name)).toEqual(["inner", "outer"]);
    const [child, root] = exp.spans;
    expect(child!.parent_span_id).toBe(root!.span_id);
    expect(child!.provenance.parent_content_hash).toBe(root!.provenance.content_hash);
  });
});
