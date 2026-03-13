# AgentCap — Architecture

## Overview
AI agent spend governance. Register agents with monthly budgets, log token usage per request, get webhook alerts at configurable thresholds, hard-cap at 100% (429 rejection). Budget forecasting, anomaly detection, cost allocation tags.

## Data Model

```
agents ─────────────────────────────
  id, name (unique), provider, model, monthly_budget_usd,
  alert_threshold_pct, webhook_url, tags (JSON array),
  current_spend_usd, created_at
  └── 1:N → usage
  └── 1:N → alerts
  └── 1:N → budget_adjustments

usage ──────────────────────────────
  id, agent_id, tokens_in, tokens_out, cost_usd,
  request_id, metadata (JSON), recorded_at

alerts ─────────────────────────────
  id, agent_id, agent_name, alert_type (warning/capped/reset/budget_increased/budget_decreased),
  spend_usd, budget_usd, spend_pct, webhook_fired, created_at

budget_adjustments ─────────────────
  id, agent_id, old_budget_usd, new_budget_usd, reason, adjusted_at
```

## Usage Flow

```
POST /agents/{id}/usage {tokens_in, tokens_out, cost_usd}
  → check agent not capped (429 if capped)
  → INSERT usage record
  → UPDATE current_spend_usd += cost_usd
  → recalculate spend_pct
  → if crossed threshold: CREATE alert + fire webhook
  → if >= 100%: status = capped, alert_type = capped
```

## Status Machine

```
ok ──(spend >= threshold_pct)──→ warning ──(spend >= 100%)──→ capped
                                                                │
capped ──(POST /reset)──→ ok  (spend = 0, alert_type = reset)  │
capped ──(POST /budget/adjust increase)──→ ok or warning        │
```

## Budget Forecasting

```
GET /agents/{id}/forecast
  → get daily spend for last 14 days
  → daily_burn = sum(cost) / active_days
  → projected_monthly = burn * 30
  → days_until_cap = remaining / daily_burn
  → trend detection: compare recent_half_avg vs older_half_avg
    ratio > 1.2 → accelerating
    ratio < 0.8 → decelerating
    else → stable
  → recommendation based on days_left + trend
```

## Anomaly Detection

```
GET /agents/{id}/anomalies?days=30&threshold=2.0
  → get daily spend for N days
  → compute baseline avg
  → flag days where cost / avg >= threshold
  → severity: >= 3x = critical, >= 2x = warning
```

## Cost Tags

Tags are stored as JSON array on agent record. Used for:
- GET /agents?tag=frontend → filter agents by tag
- GET /analytics/by-tag → aggregate spend/budget/utilization per tag
- Chargeback reporting: group costs by project/team/department

## Key Decisions

### 1. Spend-pct-based status
Status computed on read, not stored. Prevents stale state.
Budget adjustments immediately change status without migration.

### 2. Tags as JSON array
Simple, no join table. Tags are low-cardinality (5-10 per agent max).
Filtering done in Python after DB fetch — acceptable for < 10K agents.

### 3. Webhook fire-and-forget
Single attempt, 5s timeout. No retry queue.
webhook_fired flag tracks success for audit.

### 4. Budget history as separate table
Every adjustment logged with reason. Enables audit trail and compliance.
Shown via GET /agents/{id}/budget/history.
