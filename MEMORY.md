# AgentCap — Development Log

## v1.4.0 (2026-03-13)
- **Cost tags**: JSON array on agents, GET /agents?tag= filter, GET /analytics/by-tag
- **Budget history**: GET /agents/{id}/budget/history (all adjustments with reason)
- **Anomaly detection**: GET /agents/{id}/anomalies (flag days > Nx average, severity levels)
- Tags field added to AgentCreate/Update/Response
- Migration: ALTER TABLE ADD COLUMN tags with default
- Updated DEEP.md with anomaly and tag architecture
- Bumped v1.4.0

## v1.3.0
- Budget forecasting: burn rate, projected cap date, trend (accelerating/stable/decelerating)
- Provider analytics: GET /analytics/providers
- CSV export: GET /agents/{id}/usage/export/csv
- Budget adjust with audit trail: POST /agents/{id}/budget/adjust
- DEEP.md + MEMORY.md docs
- Makefile + smoke tests

## v1.2.0
- GET /dashboard (cross-agent overview, top spenders, utilization)
- GET /agents/{id}/usage/daily (daily spend breakdown)
- DELETE /agents/{id} (cascade usage + alerts)

## v1.1.0
- GET /agents/{id}/usage (audit log with metadata)
- PATCH /agents/{id} (budget/threshold/webhook update)

## v1.0.0
- Initial: agent CRUD, usage recording, budget tracking, threshold alerts, webhook firing, 429 cap
