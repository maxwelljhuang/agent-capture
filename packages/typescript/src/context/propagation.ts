/**
 * Context propagation — AsyncLocalStorage-backed "current parent span"
 * pointer. Mirrors agent_capture.context.propagation in the Python package.
 *
 * AsyncLocalStorage automatically propagates across async/await, Promise
 * chains, timers, and tasks scheduled on the Node event loop. Native
 * worker_threads / child_process boundaries do NOT propagate — use
 * `inject()` / `extract()` from ./w3c for those.
 *
 * The pointer stores an OpenSpan (the in-progress mutable state from the
 * span builder), not a finalized Span. That gives the builder access to
 * the live parent for parent_content_hash stamping at close time.
 */

import { AsyncLocalStorage } from "node:async_hooks";

import type { OpenSpan } from "../span/types.js";

interface ContextState {
  parent: OpenSpan | null;
  suppressModelCall: boolean;
}

const _storage = new AsyncLocalStorage<ContextState>();

function _state(): ContextState {
  return _storage.getStore() ?? { parent: null, suppressModelCall: false };
}

/** Return the currently-active parent OpenSpan, or null at the root. */
export function currentParent(): OpenSpan | null {
  return _state().parent;
}

/**
 * Run `fn` with `span` installed as the current parent. The previous
 * parent (if any) is restored on return — whether normal or thrown.
 *
 * Use the async variant when `fn` is async; AsyncLocalStorage handles
 * both transparently, but TypeScript inference is clearer with two
 * explicitly-typed overloads.
 */
export function spanScope<T>(span: OpenSpan, fn: () => T): T;
export function spanScope<T>(span: OpenSpan, fn: () => Promise<T>): Promise<T>;
export function spanScope<T>(span: OpenSpan, fn: () => T | Promise<T>): T | Promise<T> {
  const prev = _state();
  return _storage.run({ ...prev, parent: span }, fn);
}

/** True if SDK wrappers should skip emitting a model_call span. */
export function modelCallSuppressed(): boolean {
  return _state().suppressModelCall;
}

/**
 * Run `fn` with the model_call-suppression flag active. SDK wrappers
 * (anthropic, openai) inside the scope pass through without emitting
 * their own model_call spans — the framework adapter owns the span.
 */
export function suppressModelCallCapture<T>(fn: () => T): T;
export function suppressModelCallCapture<T>(fn: () => Promise<T>): Promise<T>;
export function suppressModelCallCapture<T>(fn: () => T | Promise<T>): T | Promise<T> {
  const prev = _state();
  return _storage.run({ ...prev, suppressModelCall: true }, fn);
}
