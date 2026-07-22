/**
 * Process-wide singletons. Mirrors agent_capture._internal.runtime.
 *
 * `configure()` registers the default SpanBuilder here so traced() and
 * tracedScope() can find it without an explicit argument.
 */

import type { SpanBuilder } from "../span/builder.js";

let _defaultBuilder: SpanBuilder | null = null;

export function setDefaultBuilder(builder: SpanBuilder | null): void {
  _defaultBuilder = builder;
}

export function defaultBuilder(): SpanBuilder | null {
  return _defaultBuilder;
}
