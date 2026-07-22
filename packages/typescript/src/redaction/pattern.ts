/**
 * Free-text PatternRedactor — applies a set of recognizers to a string.
 * Mirrors agent_capture.redaction.pattern.
 */

import { DEFAULT_RECOGNIZERS, type Match, type Recognizer } from "./patternsFinance.js";
import type { RedactionStrategy } from "./strategies.js";

export class PatternRedactor {
  constructor(
    private readonly options: {
      strategyFor: (fieldType: string) => RedactionStrategy;
      recognizers?: ReadonlyArray<Recognizer>;
    },
  ) {}

  redact(text: string): string {
    if (text === "") return text;
    const recognizers = this.options.recognizers ?? DEFAULT_RECOGNIZERS;
    let matches: Match[] = [];
    for (const r of recognizers) matches.push(...r.findAll(text));
    if (matches.length === 0) return text;
    matches = resolveOverlaps(matches);
    matches.sort((a, b) => b.start - a.start); // right-to-left
    let out = text;
    for (const m of matches) {
      const recognizer = recognizers.find((r) => r.name === m.recognizer)!;
      const replacement = this.options
        .strategyFor(recognizer.fieldType)
        .redact(m.value, { fieldType: recognizer.fieldType });
      out = out.slice(0, m.start) + replacement + out.slice(m.end);
    }
    return out;
  }
}

function resolveOverlaps(matches: Match[]): Match[] {
  const sorted = [...matches].sort((a, b) => {
    const aLen = a.end - a.start;
    const bLen = b.end - b.start;
    if (aLen !== bLen) return bLen - aLen;
    return a.start - b.start;
  });
  const keepers: Match[] = [];
  for (const m of sorted) {
    const conflict = keepers.some((k) => !(m.end <= k.start || m.start >= k.end));
    if (!conflict) keepers.push(m);
  }
  return keepers;
}
