"""Performance budget tests (Week 5).

Enforces the architecture doc §10 budgets:

- per-span overhead < 1ms p99
- memory < 100MB steady-state
- CPU < 2% sustained
- no measurable impact on agent p99 latency

Uses ``pytest-benchmark``. Mark each test ``@pytest.mark.perf`` so CI can
schedule these separately from unit/integration runs.
"""
