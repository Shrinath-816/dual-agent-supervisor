"""
The Dual-Agent Supervisor
===========================

Week 8 project: a secure, scalable dual-agent supervisor system —
built to survive a live provider-outage recovery drill.

Implements, concretely, the reliability/security/architecture
disciplines from Week 8's three-part notes:

- Part 2's Maker-Checker pattern (app/agents/): a Maker proposes an
  action; an INDEPENDENT Checker (different prompt, criteria-only
  input, no exposure to the Maker's reasoning) approves or rejects it
  before anything executes. Fails CLOSED if the checker itself errors.
- Part 3's reliability stack (app/model_gateway.py, app/circuit_breaker.py):
  per-provider circuit breakers, step-granular retries, and
  multi-provider fallback routing — this IS the mechanism the outage
  drill (scripts/run_outage_drill.py) exercises live.
- Part 3's security stack (app/security/): input-guardrail injection
  detection, PII redaction at every boundary, and least-privilege
  scoped tool permissions.
- Part 3's cost/latency levers (app/budget.py, app/rate_limiter.py,
  app/cache/semantic_cache.py): multi-scope token budgets enforced
  BEFORE each model call, token-aware rate limiting, and a semantic
  cache that explicitly excludes time-sensitive/personalized queries.
- Part 3's observability requirement (app/telemetry.py): every
  reliability/security mechanism above emits its own event when it
  activates.

See README.md for setup and usage, and week-8-part{1,2,3}-notes.md
(provided separately) for the concepts this implements.
"""
