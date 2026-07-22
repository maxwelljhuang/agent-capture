/**
 * Schema sanity test — the generated types compile and a structurally valid
 * span can be constructed. Once the TS span builder lands in Week 5, this
 * file gets replaced with deeper construction + canonicalization tests.
 */

import { describe, expect, it } from "vitest";
import { SCHEMA_VERSION, type AgentCaptureSpan } from "../src/index.js";

describe("schema", () => {
  it("exposes the SCHEMA_VERSION constant", () => {
    expect(SCHEMA_VERSION).toBe("1.0.0");
  });

  it("permits a structurally valid model_call span at the type level", () => {
    const span: AgentCaptureSpan = {
      span_id: "aaaaaaaaaaaaaaaa",
      parent_span_id: null,
      trajectory_id: "aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb",
      name: "score_application",
      type: "model_call",
      start_time: "2026-05-17T12:00:00.000000Z",
      end_time: "2026-05-17T12:00:01.000000Z",
      status: "ok",
      error: null,
      inputs: null,
      outputs: null,
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
      compliance: {
        policy_version_active: "v1.0",
        prompt_template_version: null,
        model_card_version: null,
        tool_schema_version: null,
        agent_version: "0.1.0",
        end_customer_id: "acme-bank",
        subject_id: null,
        regulatory_regime: ["ECOA"],
        retention_class: "standard",
        data_classification: "internal",
      },
      provenance: {
        content_hash: "0".repeat(64),
        parent_content_hash: null,
        schema_version: SCHEMA_VERSION,
      },
    };
    expect(span.type).toBe("model_call");
    expect(span.attributes.kind).toBe("model_call");
  });
});
