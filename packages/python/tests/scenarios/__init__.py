"""Scenario tests — schema validation against realistic compliance trajectories.

Each scenario builds the full tree of spans that a real agent flow would
produce, then asserts every Section 4 field of the architecture document is
populated and that the resulting trajectory hashes correctly chain.

These tests are the Week 1 contract: if they pass, the schema captures
everything the reporting layer will need. If the schema must change to make
them pass, that's the schema change.
"""
