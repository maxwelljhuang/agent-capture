/**
 * Redaction policy — YAML-loadable customer-owned bundle.
 * Mirrors agent_capture.redaction.policy.
 *
 * Uses `js-yaml` for parsing. The vendor never writes this file; the
 * customer's security team does, and the version is stamped on every
 * span via compliance.policy_version_active.
 */

import { readFileSync } from "node:fs";

import {
  FullRedaction,
  HmacFingerprint,
  PassThrough,
  type HmacFingerprintOptions,
  type RedactionStrategy,
} from "./strategies.js";

/**
 * The non-negotiable redaction floor. A value a recognizer flags as one of
 * these PII field types may NEVER ship in cleartext, whatever the customer
 * policy says — a `pass_through` for it is coerced up to full redaction. The
 * policy may still choose HMAC fingerprinting; it just can't disable redaction.
 * Recognized PII reaches `strategyForPattern` only via a recognizer match, so
 * flooring there keeps recognized PII out of the durable ledger in cleartext.
 * Mirrors `_PII_FLOOR_FIELD_TYPES` in the Python SDK — keep the two in sync.
 */
const PII_FLOOR_FIELD_TYPES: ReadonlySet<string> = new Set([
  "ssn",
  "routing_number",
  "account_number",
  "micr",
  "date_of_birth",
]);
const FLOOR_STRATEGY: RedactionStrategy = new FullRedaction();

export interface FieldRule {
  fieldName: string;
  strategy: string;
}

export interface PatternRule {
  fieldType: string;
  strategy: string;
}

export interface PolicyOptions {
  version: string;
  defaultStrategy: string;
  strategies: Record<string, RedactionStrategy>;
  fieldRules?: ReadonlyArray<FieldRule>;
  patternRules?: ReadonlyArray<PatternRule>;
}

export class Policy {
  readonly version: string;
  readonly defaultStrategy: string;
  readonly strategies: Record<string, RedactionStrategy>;
  readonly fieldRules: ReadonlyArray<FieldRule>;
  readonly patternRules: ReadonlyArray<PatternRule>;

  constructor(options: PolicyOptions) {
    this.version = options.version;
    this.defaultStrategy = options.defaultStrategy;
    this.strategies = options.strategies;
    this.fieldRules = options.fieldRules ?? [];
    this.patternRules = options.patternRules ?? [];
  }

  strategyForField(fieldName: string): RedactionStrategy | null {
    const lower = fieldName.toLowerCase();
    for (const r of this.fieldRules) {
      if (r.fieldName.toLowerCase() === lower) return this.resolve(r.strategy);
    }
    return null;
  }

  /**
   * Strategy for a recognizer's field type. Falls back to the default, then
   * applies the redaction floor: a recognized-PII field type can never resolve
   * to PassThrough (cleartext) — it is coerced to full redaction. HMAC allowed.
   */
  strategyForPattern(fieldType: string): RedactionStrategy {
    let resolved = this.resolve(this.defaultStrategy);
    for (const r of this.patternRules) {
      if (r.fieldType === fieldType) {
        resolved = this.resolve(r.strategy);
        break;
      }
    }
    if (PII_FLOOR_FIELD_TYPES.has(fieldType) && resolved instanceof PassThrough) {
      return FLOOR_STRATEGY;
    }
    return resolved;
  }

  private resolve(name: string): RedactionStrategy {
    const s = this.strategies[name];
    if (s === undefined) {
      throw new Error(`Policy references undefined strategy ${JSON.stringify(name)}`);
    }
    return s;
  }
}

type StrategyBuilder = (cfg: Record<string, unknown>) => RedactionStrategy;

const STRATEGY_BUILDERS: Record<string, StrategyBuilder> = {
  full: () => new FullRedaction(),
  pass_through: () => new PassThrough(),
  hmac: (cfg) => {
    const opts: HmacFingerprintOptions = {};
    if (typeof cfg.key_env === "string") opts.keyEnv = cfg.key_env;
    if (typeof cfg.key === "string") opts.key = cfg.key;
    if (typeof cfg.truncate === "number") opts.truncate = cfg.truncate;
    return new HmacFingerprint(opts);
  },
};

export function parsePolicy(doc: Record<string, unknown>): Policy {
  const version = doc.version;
  if (typeof version !== "string" || version === "") {
    throw new Error("Policy must declare a non-empty top-level 'version' string.");
  }
  const rawStrategies = (doc.strategies ?? {}) as Record<string, unknown>;
  if (typeof rawStrategies !== "object" || rawStrategies === null || Array.isArray(rawStrategies)) {
    throw new Error("Policy 'strategies' must be a mapping.");
  }
  const strategies: Record<string, RedactionStrategy> = {};
  for (const [name, cfg] of Object.entries(rawStrategies)) {
    if (typeof cfg !== "object" || cfg === null) {
      throw new Error(`Strategy ${JSON.stringify(name)} config must be a mapping.`);
    }
    const cfgObj = cfg as Record<string, unknown>;
    const t = cfgObj.type;
    if (typeof t !== "string" || !(t in STRATEGY_BUILDERS)) {
      throw new Error(
        `Strategy ${JSON.stringify(name)} declares unknown type ${JSON.stringify(t)}. ` +
          `Allowed: ${Object.keys(STRATEGY_BUILDERS).join(", ")}`,
      );
    }
    strategies[name] = STRATEGY_BUILDERS[t]!(cfgObj);
  }

  const defaultStrategy =
    typeof doc.default_strategy === "string" ? doc.default_strategy : "full";
  if (!(defaultStrategy in strategies)) {
    if (defaultStrategy === "full") {
      strategies["full"] = new FullRedaction();
    } else {
      throw new Error(
        `default_strategy ${JSON.stringify(defaultStrategy)} not present in 'strategies'.`,
      );
    }
  }

  const fieldRules = parseRules(doc.field_rules, "field_rules", "field_name", "fieldName");
  const patternRules = parseRules(doc.pattern_rules, "pattern_rules", "field_type", "fieldType");

  for (const r of fieldRules) {
    if (!(r.strategy in strategies)) {
      throw new Error(`field_rule uses undefined strategy ${JSON.stringify(r.strategy)}`);
    }
  }
  for (const r of patternRules) {
    if (!(r.strategy in strategies)) {
      throw new Error(`pattern_rule uses undefined strategy ${JSON.stringify(r.strategy)}`);
    }
  }

  return new Policy({
    version,
    defaultStrategy,
    strategies,
    fieldRules: fieldRules as FieldRule[],
    patternRules: patternRules as PatternRule[],
  });
}

function parseRules<K extends "fieldName" | "fieldType">(
  raw: unknown,
  label: string,
  yamlKey: "field_name" | "field_type",
  jsKey: K,
): ReadonlyArray<{ strategy: string } & Record<K, string>> {
  if (raw === undefined || raw === null) return [];
  if (!Array.isArray(raw)) {
    throw new Error(`Policy ${JSON.stringify(label)} must be a list.`);
  }
  return raw.map((r) => {
    if (typeof r !== "object" || r === null) {
      throw new Error(`${label} entries must be mappings.`);
    }
    const rec = r as Record<string, unknown>;
    if (typeof rec[yamlKey] !== "string" || typeof rec.strategy !== "string") {
      throw new Error(
        `${label} entries must have ${JSON.stringify(yamlKey)} and 'strategy' strings.`,
      );
    }
    return { [jsKey]: rec[yamlKey] as string, strategy: rec.strategy } as { strategy: string } & Record<K, string>;
  });
}

/**
 * Parse a YAML policy file. Requires `js-yaml` to be installed (it's in
 * the package's optional deps; tests stub it by passing parsed docs to
 * {@link parsePolicy} directly).
 */
export async function loadPolicy(path: string): Promise<Policy> {
  const yaml = await import("js-yaml");
  const raw = readFileSync(path, "utf-8");
  const doc = yaml.load(raw);
  if (typeof doc !== "object" || doc === null) {
    throw new Error(`Policy at ${path} must be a YAML mapping at the root.`);
  }
  return parsePolicy(doc as Record<string, unknown>);
}

export function passThroughPolicy(version: string = "pass-through"): Policy {
  return new Policy({
    version,
    defaultStrategy: "pass_through",
    strategies: { pass_through: new PassThrough() },
  });
}
