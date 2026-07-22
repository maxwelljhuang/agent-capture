/**
 * Exponential backoff retry helper.
 *
 * Mirrors agent_capture.exporter.retry. Used by HTTPExporter for transient
 * failures. Permanent failures (4xx) skip retries via the `retryable`
 * predicate.
 */

export interface RetryPolicy {
  maxAttempts: number;
  baseDelayMs: number;
  maxDelayMs: number;
  multiplier: number;
  jitter: number;
  /** Override for tests. */
  sleep?: (ms: number) => Promise<void>;
}

export const defaultRetryPolicy: RetryPolicy = {
  maxAttempts: 5,
  baseDelayMs: 500,
  maxDelayMs: 30_000,
  multiplier: 2.0,
  jitter: 0.25,
};

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function delayForAttempt(policy: RetryPolicy, attempt: number): number {
  const raw = Math.min(
    policy.maxDelayMs,
    policy.baseDelayMs * Math.pow(policy.multiplier, attempt),
  );
  const jit = raw * policy.jitter * (2 * Math.random() - 1);
  return Math.max(0, raw + jit);
}

export async function withRetry<T>(
  fn: () => Promise<T>,
  options: {
    policy?: RetryPolicy;
    retryable?: (exc: unknown) => boolean;
  } = {},
): Promise<T> {
  const policy = options.policy ?? defaultRetryPolicy;
  const retryable = options.retryable ?? (() => true);
  const sleep = policy.sleep ?? defaultSleep;
  let lastError: unknown;
  for (let attempt = 0; attempt < policy.maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (exc) {
      lastError = exc;
      if (attempt === policy.maxAttempts - 1 || !retryable(exc)) {
        throw exc;
      }
      await sleep(delayForAttempt(policy, attempt));
    }
  }
  throw lastError;
}
