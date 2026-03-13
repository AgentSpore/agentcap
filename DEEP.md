# AgentCap — Architecture (v1.5.0)

## Overview
AI agent spend governance platform. Per-agent budgets, rate limiting, webhook alerts, forecasting, anomaly detection, usage comparison.

## Stack
- FastAPI + aiosqlite + Pydantic v2 + httpx

## Database Tables
- **agents** — name, provider, model, budget, threshold, webhook, tags, spend
- **usage** — per-request cost/token tracking
- **alerts** — budget warnings/caps with acknowledgment
- **budget_adjustments** — audit trail
- **rate_limits** — RPM/TPH per agent (v1.5.0)

## Key Features
- Dashboard with top spenders and utilization
- Budget forecasting with daily burn rate
- Provider breakdown analytics
- Tag-based cost allocation
- Spend anomaly detection
- Rate limiting with sliding window (v1.5.0)
- Cross-agent usage comparison (v1.5.0)
- Alert acknowledgment workflow (v1.5.0)

## Endpoints (27 total)
### Dashboard: GET /dashboard
### Agents: POST/GET/GET/{id}/PATCH/DELETE /agents
### Usage: POST/{id}/usage, GET/{id}/usage, GET/{id}/usage/daily, GET/{id}/usage/export/csv
### Budget: POST/{id}/reset, GET/{id}/forecast, POST/{id}/budget/adjust, GET/{id}/budget/history
### Stats: GET/{id}/stats, GET/{id}/anomalies
### Rate Limits (v1.5.0): PUT/{id}/rate-limit, GET/{id}/rate-limit
### Comparison (v1.5.0): POST /analytics/compare
### Analytics: GET /analytics/providers, GET /analytics/by-tag
### Alerts: GET /alerts, GET/{id}/alerts, POST /alerts/{id}/acknowledge (v1.5.0)
### Health: GET /health
