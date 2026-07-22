/**
 * Finance-specific pattern recognizers. Mirrors
 * agent_capture.redaction.patterns_finance.
 *
 * Coverage:
 * - US SSN — dashed and undashed, SSA area-rule exclusions
 * - ABA routing — 9 digits, mod-10 checksum validated
 * - US bank account — context-gated 8-17 digits
 * - MICR line — basic ASCII encoding
 * - DOB — cue-word-gated dates
 */

export interface Match {
  start: number;
  end: number;
  value: string;
  recognizer: string;
}

export interface Recognizer {
  name: string;
  fieldType: string;
  findAll(text: string): Match[];
}

function abaChecksumValid(s: string): boolean {
  const digits = [...s].filter((c) => /\d/.test(c)).map((c) => parseInt(c, 10));
  if (digits.length !== 9) return false;
  const weights = [3, 7, 1, 3, 7, 1, 3, 7, 1];
  let sum = 0;
  for (let i = 0; i < 9; i++) sum += digits[i]! * weights[i]!;
  return sum % 10 === 0;
}

function regexRecognizer(
  name: string,
  fieldType: string,
  pattern: RegExp,
  validator?: (matched: string) => boolean,
): Recognizer {
  return {
    name,
    fieldType,
    findAll(text: string): Match[] {
      const out: Match[] = [];
      const re = new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : pattern.flags + "g");
      let m: RegExpExecArray | null;
      while ((m = re.exec(text)) !== null) {
        if (validator !== undefined && !validator(m[0])) continue;
        out.push({ start: m.index, end: m.index + m[0].length, value: m[0], recognizer: name });
        if (m[0].length === 0) re.lastIndex++; // guard against zero-width loops
      }
      return out;
    },
  };
}

export const US_SSN: Recognizer = regexRecognizer(
  "us_ssn",
  "ssn",
  // Standard formats, excluding obviously-invalid groups (000, 666, 9xx area; 00 group; 0000 serial).
  /\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b|\b(?!000|666|9\d{2})\d{3}(?!00)\d{2}(?!0000)\d{4}\b/g,
);

export const ABA_ROUTING: Recognizer = regexRecognizer(
  "aba_routing",
  "routing_number",
  /\b\d{9}\b/g,
  abaChecksumValid,
);

export const US_BANK_ACCOUNT: Recognizer = regexRecognizer(
  "us_bank_account",
  "account_number",
  /\b(?:account(?:\s+(?:number|no\.?|#))?|acct(?:\.\s*(?:no\.?|#))?)\s*[:#]?\s*(\d[\d-]{7,16}\d)\b/gi,
);

export const MICR_LINE: Recognizer = regexRecognizer(
  "micr_line",
  "micr",
  /[⑆⑇⑈⑉][^⑆⑇⑈⑉]+[⑆⑇⑈⑉]|A\d{9}A\s*[A-D\d-]{5,}/g,
);

export const DOB: Recognizer = regexRecognizer(
  "dob",
  "date_of_birth",
  /\b(?:dob|d\.o\.b\.?|date\s+of\s+birth|born(?:\s+on)?)\b[:\s-]*((?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}|(?:19|20)\d{2}[/-](?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01]))/gi,
);

export const DEFAULT_RECOGNIZERS: ReadonlyArray<Recognizer> = [
  US_SSN,
  ABA_ROUTING,
  US_BANK_ACCOUNT,
  MICR_LINE,
  DOB,
];
