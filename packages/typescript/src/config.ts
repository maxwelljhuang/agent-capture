/**
 * Process-level configure() helper. Wires the SpanBuilder and registers
 * it as the process-wide default so traced() / tracedScope() can find it.
 *
 * Mirrors agent_capture.config.configure.
 */

import type { SpanExporter } from "./exporter/base.js";
import { setDefaultBuilder } from "./internal/runtime.js";
import type { RedactionFilter } from "./redaction/filter.js";
import type { ComplianceMetadata } from "./schema/span.js";
import { SpanBuilder } from "./span/builder.js";

export interface ConfigureOptions {
  exporter: SpanExporter;
  defaultCompliance?: ComplianceMetadata;
  redactionFilter?: RedactionFilter;
}

export function configure(options: ConfigureOptions): SpanBuilder {
  const builderOpts: { defaultCompliance?: ComplianceMetadata; redactionFilter?: RedactionFilter } = {};
  if (options.defaultCompliance !== undefined) {
    builderOpts.defaultCompliance = options.defaultCompliance;
  }
  if (options.redactionFilter !== undefined) {
    builderOpts.redactionFilter = options.redactionFilter;
  }
  const builder = new SpanBuilder(options.exporter, builderOpts);
  setDefaultBuilder(builder);
  return builder;
}
