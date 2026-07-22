/**
 * AsyncLocalStorage context propagation tests.
 */

import { describe, expect, it } from "vitest";

import {
  currentParent,
  modelCallSuppressed,
  spanScope,
  suppressModelCallCapture,
} from "../src/context/propagation.js";
import { extract, inject, TRACEPARENT_HEADER } from "../src/context/w3c.js";
import type { OpenSpan } from "../src/span/types.js";

function fakeOpenSpan(spanId: string, trajId: string): OpenSpan {
  return {
    spanId,
    parentSpanId: null,
    trajectoryId: trajId,
    name: "x",
    type: "planner_step",
    startTime: new Date(),
    attributes: {
      kind: "planner_step",
      decision_rationale: null,
      options_considered: [],
      chosen_option: null,
    },
    compliance: {
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
    },
    inputs: null,
    parent: null,
    pending: [],
  };
}

describe("context propagation", () => {
  it("baseline parent is null", () => {
    expect(currentParent()).toBeNull();
  });

  it("spanScope installs and restores parent", () => {
    const a = fakeOpenSpan("aaaaaaaaaaaaaaaa", "a".repeat(32));
    expect(currentParent()).toBeNull();
    spanScope(a, () => {
      expect(currentParent()).toBe(a);
    });
    expect(currentParent()).toBeNull();
  });

  it("spanScope nests cleanly", () => {
    const a = fakeOpenSpan("a".repeat(16), "a".repeat(32));
    const b = fakeOpenSpan("b".repeat(16), "a".repeat(32));
    spanScope(a, () => {
      expect(currentParent()).toBe(a);
      spanScope(b, () => {
        expect(currentParent()).toBe(b);
      });
      expect(currentParent()).toBe(a);
    });
  });

  it("propagates across awaits", async () => {
    const a = fakeOpenSpan("a".repeat(16), "a".repeat(32));
    await spanScope(a, async () => {
      await new Promise((r) => setTimeout(r, 0));
      expect(currentParent()).toBe(a);
    });
    expect(currentParent()).toBeNull();
  });

  it("suppressModelCallCapture toggles the flag inside its scope", () => {
    expect(modelCallSuppressed()).toBe(false);
    suppressModelCallCapture(() => {
      expect(modelCallSuppressed()).toBe(true);
    });
    expect(modelCallSuppressed()).toBe(false);
  });
});

describe("W3C trace context", () => {
  it("inject returns empty when no parent", () => {
    expect(inject()).toEqual({});
  });

  it("inject + extract round-trip", () => {
    const a = fakeOpenSpan("aaaaaaaaaaaaaaaa", "0123456789abcdef".repeat(2));
    spanScope(a, () => {
      const headers = inject();
      expect(headers[TRACEPARENT_HEADER]).toBe(
        `00-${"0123456789abcdef".repeat(2)}-aaaaaaaaaaaaaaaa-01`,
      );
      const remote = extract(headers);
      expect(remote).toEqual({
        trajectoryId: "0123456789abcdef".repeat(2),
        spanId: "aaaaaaaaaaaaaaaa",
        sampled: true,
      });
    });
  });

  it("extract returns null for malformed headers", () => {
    expect(extract({ traceparent: "garbage" })).toBeNull();
    expect(extract({})).toBeNull();
    expect(extract({ traceparent: "ff-" + "a".repeat(32) + "-" + "a".repeat(16) + "-01" })).toBeNull();
  });

  it("extract is case-insensitive", () => {
    const a = fakeOpenSpan("aaaaaaaaaaaaaaaa", "a".repeat(32));
    spanScope(a, () => {
      const headers = inject();
      const upper: Record<string, string> = {};
      for (const [k, v] of Object.entries(headers)) upper[k.toUpperCase()] = v;
      expect(extract(upper)).not.toBeNull();
    });
  });
});
