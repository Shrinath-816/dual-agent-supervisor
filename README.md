# The Dual-Agent Supervisor — Week 8 Project

A secure, scalable dual-agent supervisor system — built to survive a
live provider-outage recovery drill. This is Week 8 of a 10-week
self-study AI Engineering program; see `week-8-part1-notes.md`,
`week-8-part2-notes.md`, and `week-8-part3-notes.md` (provided
separately) for the concepts this project implements end to end.

## What this project demonstrates

| Concept | Where it lives |
|---|---|
| Maker-Checker pattern, with genuine independence | `app/agents/maker.py`, `app/agents/checker.py` |
| Fail-closed on checker failure | `app/agents/supervisor.py` (see `test_checker_failure_fails_closed_not_open`) |
| Multi-provider fallback + circuit breakers | `app/model_gateway.py`, `app/circuit_breaker.py` |
| **The live outage drill** | `scripts/run_outage_drill.py` |
| Least-privilege tool permissions (risk-tiered) | `app/security/tool_permissions.py` |
| Prompt injection defense (heuristic + delimiting) | `app/security/injection_guard.py` |
| PII redaction at boundaries | `app/security/pii_redactor.py` |
| Multi-scope token budgets | `app/budget.py` |
| Token-aware rate limiting | `app/rate_limiter.py` |
| Semantic caching with scoped exclusion | `app/cache/semantic_cache.py` |
| Observability for every mechanism above | `app/telemetry.py` |

## Project structure

```
dual-agent-supervisor/
├── app/
│   ├── main.py                      # FastAPI app
│   ├── config.py                    # Settings + provider fallback chain
│   ├── telemetry.py                 # Event log every mechanism writes to
│   ├── circuit_breaker.py           # Per-provider breaker (closed/open/half-open)
│   ├── model_gateway.py             # Fallback routing + retries + timeouts + drill hooks
│   ├── budget.py                    # Multi-scope token budget enforcement
│   ├── rate_limiter.py              # Token-bucket, token-aware rate limiting
│   ├── tools.py                     # Example tools with declared risk tiers
│   ├── security/
│   │   ├── injection_guard.py       # Heuristic injection detection + delimiting
│   │   ├── pii_redactor.py          # Regex-based PII detection/redaction
│   │   └── tool_permissions.py      # Least-privilege, risk-tiered tool access
│   ├── cache/
│   │   └── semantic_cache.py        # Embedding-similarity cache, scoped exclusion
│   └── agents/
│       ├── maker.py                 # Proposes actions/responses
│       ├── checker.py               # INDEPENDENTLY reviews proposed actions
│       └── supervisor.py            # Orchestrates everything above
├── scripts/
│   └── run_outage_drill.py          # THE LIVE PROVIDER-OUTAGE DRILL
├── tests/                           # All pure logic — no network, no API key needed
├── requirements.txt
├── pytest.ini
├── Dockerfile
├── .env.example
└── README.md
```

## Setup

1. Get an OpenRouter API key: https://openrouter.ai/keys
2. Verify current model slugs at https://openrouter.ai/models — `PRIMARY_MODEL`/`BACKUP_MODEL`/`CHECKER_MODEL` in `.env.example` are placeholders and must support tool calling.
3. `cp .env.example .env` and fill in your real key.
4. `pip install -r requirements.txt`
5. `uvicorn app.main:app --reload`

## Running the live outage drill

This is the direct, executable proof of this week's brief:

```bash
python scripts/run_outage_drill.py
```

It will:
1. Confirm the primary provider handles normal requests.
2. **Simulate a real primary-provider outage** (deterministic fault injection — no need to wait for or risk an actual outage).
3. Verify the circuit breaker opens, traffic automatically fails over to the backup provider, and a side-effecting action (a refund) *still* goes through Checker review during the outage — reliability failover never silently bypasses security.
4. Simulate recovery, wait out the breaker's reset window, and verify the breaker closes again via a successful half-open probe.
5. Print a pass/fail report and exit non-zero on any failed assertion — wire this into CI as a real reliability gate (the Week 5 regression-gate pattern, applied to a live drill).

The drill needs a real `OPENROUTER_API_KEY`: the backup provider genuinely serves requests while the primary is "down."

## Usage

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "My order ORD-1001 arrived broken, please refund it.", "tenant_id": "acme"}'

curl http://localhost:8000/breaker-status
curl http://localhost:8000/telemetry
```

## Running tests

```bash
pytest
```
Every test is pure logic with fakes/doubles — no network, no API key, no running server needed. `test_supervisor.py::test_checker_failure_fails_closed_not_open` is the single most important test here: it proves a Checker outage blocks the action rather than silently approving it.

## Known limitations (intentional — noted extensions)

- The Checker shares the Maker's `ModelGateway`/provider chain in this build's wiring (`app/main.py`) rather than having a fully separate gateway pointed at `checker_model` — see that file's inline note on the production extension.
- A Checker rejection escalates immediately rather than feeding the rejection reason back to the Maker for one automated revision attempt — kept simple and deterministic for the drill; week-8-part2-notes.md section 8 describes the fuller revise-and-resubmit loop.
- PII detection is pattern/regex-based (structured PII only) — see week-8-part3-notes.md section 5 for NER/LLM-based detection as a natural upgrade.
- The semantic cache's embedding vectors must be supplied by the caller (no embedding client is bundled) — see Weeks 2-3's embedding client for how to wire one in.
- Telemetry is an in-memory event log, not a full Week 5 Langfuse/OTel integration.
- `tool_timeout_seconds` and `overall_request_timeout_seconds` are defined in settings but the simplified loop only enforces the per-model-call timeout; wiring the other two is a natural next step (week-8-part3-notes.md section 9).
