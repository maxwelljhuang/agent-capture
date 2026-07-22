// Flat config (ESLint 9+). Lints the TS SDK source + tests.
// Run with `pnpm lint`; enforced in CI (the `typescript` job).
import js from "@eslint/js";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";

export default [
  // `src/schema/span.ts` is generated from schemas/span.schema.json — never lint it.
  { ignores: ["dist/**", "coverage/**", "node_modules/**", "src/schema/span.ts"] },
  js.configs.recommended,
  {
    files: ["**/*.ts"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: "module",
    },
    plugins: { "@typescript-eslint": tsPlugin },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      // TypeScript itself resolves identifiers; the core rule false-positives
      // on type-only and ambient names.
      "no-undef": "off",
      // Allow intentionally-unused args/vars when prefixed with `_`.
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // The core rule false-positives on TS function overload signatures;
      // the typescript-eslint version understands them.
      "no-redeclare": "off",
      "@typescript-eslint/no-redeclare": "error",
    },
  },
];
