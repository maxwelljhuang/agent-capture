/**
 * Span schema version. Must match
 * agent_capture.schema.provenance.SCHEMA_VERSION in the Python package and
 * the $id version segment in schemas/span.schema.json.
 *
 * The CI schema-sync workflow asserts these stay in lockstep.
 */
export const SCHEMA_VERSION = "1.0.0";
