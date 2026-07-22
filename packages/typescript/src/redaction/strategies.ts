/**
 * Replacement strategies. Mirrors agent_capture.redaction.strategies.
 *
 * - FullRedaction: replace with `[REDACTED:<field_type>]`
 * - HmacFingerprint: replace with HMAC-SHA256 hex of the value, with
 *   the customer-managed (BYOK via KMS) key injected at use time.
 *   Deterministic so auditors can re-fingerprint a candidate value to
 *   prove the agent saw it without storing the original.
 * - PassThrough: return value verbatim (use only when policy explicitly
 *   classifies a field as non-sensitive).
 */

import { createHmac } from "node:crypto";

export interface RedactionStrategy {
  readonly name: string;
  redact(value: string, opts: { fieldType: string }): string;
}

export class FullRedaction implements RedactionStrategy {
  readonly name = "full";
  redact(_value: string, { fieldType }: { fieldType: string }): string {
    return `[REDACTED:${fieldType}]`;
  }
}

export class PassThrough implements RedactionStrategy {
  readonly name = "pass_through";
  redact(value: string, _opts: { fieldType: string }): string {
    return value;
  }
}

export interface HmacFingerprintOptions {
  /** Env var holding the HMAC key. Read at each redact() so rotations apply. */
  keyEnv?: string;
  /** Inline key for tests ONLY. Production must use keyEnv. */
  key?: string | Buffer;
  /** Hex chars to keep in the fingerprint. Default 32 (128 bits). */
  truncate?: number;
}

export class HmacFingerprint implements RedactionStrategy {
  readonly name = "hmac";
  private readonly truncate: number;

  constructor(private readonly options: HmacFingerprintOptions = {}) {
    this.truncate = options.truncate ?? 32;
  }

  redact(value: string, { fieldType }: { fieldType: string }): string {
    const key = this.resolveKey();
    const digest = createHmac("sha256", key).update(value, "utf-8").digest("hex");
    return `[FP:${digest.slice(0, this.truncate)}:${fieldType}]`;
  }

  private resolveKey(): Buffer {
    if (this.options.key !== undefined) {
      return typeof this.options.key === "string"
        ? Buffer.from(this.options.key, "utf-8")
        : this.options.key;
    }
    if (this.options.keyEnv === undefined) {
      throw new Error(
        "HmacFingerprint requires either { key } (tests) or { keyEnv } (production).",
      );
    }
    const raw = process.env[this.options.keyEnv];
    if (raw === undefined || raw === "") {
      throw new Error(
        `HmacFingerprint: env var ${JSON.stringify(this.options.keyEnv)} is unset or empty.`,
      );
    }
    return Buffer.from(raw, "utf-8");
  }
}
