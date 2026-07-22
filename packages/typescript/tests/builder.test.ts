/**
 * SpanBuilder hash-chain tests — leaves-first emission, parent stamping.
 */

import { describe, expect, it } from "vitest";

import { spanScope } from "../src/context/propagation.js";
import type { SpanExporter } from "../src/exporter/base.js";
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
  readonly spans: AgentCaptureSpan[] = [];
  export(s: AgentCaptureSpan): void {
    this.spans.push(s);
  }
  async shutdown(): Promise<void> {}
}

function newBuilder() {
  const exp = new Capture();
  return { exp, builder: new SpanBuilder(exp, { defaultCompliance: compliance }) };
}

describe("SpanBuilder", () => {
  it("ships root span immediately when no parent", () => {
    const { exp, builder } = newBuilder();
    const root = builder.open({
      name: "root",
      type: "planner_step",
      attributes: { kind: "planner_step", decision_rationale: null, options_considered: [], chosen_option: null },
    });
    builder.close(root, { outputs: { ok: true } });
    expect(exp.spans.length).toBe(1);
    expect(exp.spans[0]!.parent_span_id).toBeNull();
    expect(exp.spans[0]!.provenance.parent_content_hash).toBeNull();
    expect(exp.spans[0]!.provenance.content_hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it("buffers child with parent and emits leaves-first with linked hashes", () => {
    const { exp, builder } = newBuilder();
    const root = builder.open({
      name: "root",
      type: "planner_step",
      attributes: { kind: "planner_step", decision_rationale: null, options_considered: [], chosen_option: null },
    });
    spanScope(root, () => {
      const child = builder.open({
        name: "model",
        type: "model_call",
        attributes: {
          kind: "model_call",
          model_name: "claude-opus-4-7",
          model_version: null,
          provider: "anthropic",
          prompt_template_id: null,
          prompt_template_version: null,
          temperature: null,
          max_tokens: null,
          input_tokens: null,
          output_tokens: null,
          total_tokens: null,
        },
      });
      builder.close(child, { outputs: { text: "ok" } });
      // Child has finalized but NOT shipped yet — buffered with parent.
      expect(exp.spans.length).toBe(0);
    });
    builder.close(root);
    // Now both shipped — child first (leaf), then root.
    expect(exp.spans.length).toBe(2);
    const [emittedChild, emittedRoot] = exp.spans;
    expect(emittedRoot!.parent_span_id).toBeNull();
    expect(emittedChild!.parent_span_id).toBe(emittedRoot!.span_id);
    expect(emittedChild!.trajectory_id).toBe(emittedRoot!.trajectory_id);
    expect(emittedChild!.provenance.parent_content_hash).toBe(
      emittedRoot!.provenance.content_hash,
    );
  });

  it("close() never throws even if the exporter raises", () => {
    const exploding: SpanExporter = {
      export() {
        throw new Error("destination on fire");
      },
      async shutdown() {},
    };
    const builder = new SpanBuilder(exploding, { defaultCompliance: compliance });
    const root = builder.open({
      name: "x",
      type: "planner_step",
      attributes: { kind: "planner_step", decision_rationale: null, options_considered: [], chosen_option: null },
    });
    // close() catches the exporter's exception and returns null. The agent
    // is never exposed to internal failures.
    expect(() => builder.close(root)).not.toThrow();
  });
});
