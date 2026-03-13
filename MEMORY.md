# AgentCap — Development Log

## v1.5.0 (2026-03-13)
- Rate limiting profiles: configurable RPM/TPH per agent, sliding window enforcement, 429 on exceed
- Usage comparison: POST /analytics/compare with 2-10 agents, period-based stats
- Alert acknowledgment: POST /alerts/{id}/acknowledge, filter by type/ack status
- 27 endpoints total

## v1.4.0
- Cost allocation tags (up to 10 per agent)
- Budget adjustment history with audit trail
- Spend anomaly detection (configurable threshold)
- Tag analytics breakdown

## v1.3.0
- Budget forecasting with daily burn rate
- Provider breakdown analytics
- CSV usage export
- Budget adjustment with reason tracking

## v1.2.0
- Dashboard with top 5 spenders
- Daily spend trends
- Spend statistics per agent

## v1.0.0
- Agent CRUD with provider/model
- Usage recording with cost tracking
- Budget alerts with webhook notifications
- Budget reset
