#!/usr/bin/env bash
# Generate TypeScript types from schemas/span.schema.json.
#
# Run from the repo root:
#     ./scripts/generate_ts_types.sh
#
# Requires pnpm install to have been run at the repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="${REPO_ROOT}/schemas/span.schema.json"
OUT_DIR="${REPO_ROOT}/packages/typescript/src/schema"
OUT_FILE="${OUT_DIR}/span.ts"

if [[ ! -f "${SCHEMA}" ]]; then
  echo "schemas/span.schema.json missing; run scripts/generate_schema.py first" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"

cd "${REPO_ROOT}"
npx --yes json-schema-to-typescript@^15 \
  --no-additionalProperties \
  --bannerComment "/* eslint-disable */
/* This file is generated. Do not edit.
 * Source: schemas/span.schema.json
 * Regenerate: ./scripts/generate_ts_types.sh
 */" \
  "${SCHEMA}" \
  > "${OUT_FILE}"

echo "wrote ${OUT_FILE#${REPO_ROOT}/}"
