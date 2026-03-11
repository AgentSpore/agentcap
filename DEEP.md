# AgentCap — Architecture Deep Dive

## Overview

AgentCap is an AI agent spend governance service. It tracks per-agent budgets, monitors real-time usage, fires webhook alerts on threshold breaches, and caps agents that exceed their monthly budget.

**Tech stack**: FastAPI + aiosqlite + Pydantic v2 + httpx

## Data Model

```
agents
├── id (PK, autoincrement)
├── name (UNIQUE)
├── provider (openai | anthropic | cohere | custom)
├── model
├── monthly_budget_usd
├── alert_threshold_pct (default 80%)
├── webhook_url (optional)
├── current_spend_usd (running total)
└── created_at

usage
├── id (PK)
├── agent_id (FK → agents)
├── tokens_in / tokens_out
├── cost_usd
├── request_id (optional, for dedup/tracing)
├── metadata (JSON, optional)
└── recorded_at

alerts
├── id (PK)
├── agent_id (FK → agents)
├── agent_name (denormalized for fast reads)
├── alert_type (warning | capped | reset | budget_increased | budget_decreased)
├── spend_usd / budget_usd / spend_pct
├── webhook_fired (bool)
└── created_at

budget_adjustments (v1.3.0)
├── id (PK)
├── agent_id (FK → agents)
├── old_budget_usd / new_budget_usd
├── reason
└── adjusted_at
```

## Key Design Decisions

### Status Derivation
Agent status (`ok` / `warning` / `capped`) is **computed at read time** from `current_spend_usd` and `monthly_budget_usd`, not stored. This avoids drift between stored status and actual numbers.

### Alert Deduplication
Alerts fire only on the threshold-crossing request: the system checks if the previous spend was below threshold and current is above. This prevents duplicate alerts on subsequent requests.

### Webhook Fire-and-Forget
Webhook delivery is best-effort with a 5-second timeout. The `webhook_fired` flag records whether delivery succeeded. Failed webhooks do not block usage recording.

### Cascade Delete
Deleting an agent removes all associated usage records, alerts, and budget adjustments. This keeps the DB clean but means historical data is lost — intended for dev/testing. Production deployments should soft-delete.

### Budget Forecasting (v1.3.0)
- Burns rate calculated from last 14 days of daily aggregates
- Trend detection compares recent half vs older half (>20% change = accelerating/decelerating)
- Recommendations are tiered: critical (<3 days), warning (<7 days), trend-based, or stable

### Provider Analytics (v1.3.0)
Aggregated from agent records (not usage). Shows per-provider agent count, total spend, utilization, and models in use. Identifies highest-spend and most-efficient providers.

## API Structure

```
/dashboard                          GET   — cross-agent overview
/agents                             POST  — register agent
/agents                             GET   — list all agents
/agents/{id}                        GET   — agent detail
/agents/{id}                        PATCH — update budget/threshold/webhook
/agents/{id}                        DELETE — cascade delete
/agents/{id}/usage                  POST  — record usage (201, 429 if capped)
/agents/{id}/usage                  GET   — usage log (?limit=)
/agents/{id}/usage/daily            GET   — daily spend (?days=30)
/agents/{id}/usage/export/csv       GET   — CSV export
/agents/{id}/reset                  POST  — reset spend to 0
/agents/{id}/stats                  GET   — aggregate stats
/agents/{id}/forecast               GET   — burn rate + cap projection
/agents/{id}/budget/adjust          POST  — adjust with audit trail
/analytics/providers                GET   — provider breakdown
/alerts                             GET   — all alerts
/agents/{id}/alerts                 GET   — agent alerts
/health                             GET   — health check
```

## Error Handling

| Code | Meaning |
|------|---------|
| 201  | Created (agent, usage) |
| 204  | Deleted |
| 404  | Agent/resource not found |
| 409  | Duplicate agent name |
| 429  | Agent is capped — reset required |

## Performance Notes

- All queries use indexed PKs and FKs
- Dashboard aggregates across all agents — O(N) but fine for <1000 agents
- Daily spend uses `date()` SQLite function — no index on computed column
- For high-throughput usage recording, consider WAL mode: `PRAGMA journal_mode=WAL`
