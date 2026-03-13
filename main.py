from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
import aiosqlite
from models import (
    AgentCreate, AgentUpdate, AgentResponse, UsageRecord, UsageResponse,
    UsageDetail, BudgetAlert, SpendStats,
    DashboardResponse, DailySpendEntry,
    ForecastResponse, ProviderBreakdownResponse,
    BudgetAdjustment, BudgetAdjustmentResponse,
    BudgetHistoryEntry, TagAnalyticsResponse, AnomalyResponse,
    RateLimitCreate, RateLimitResponse,
    AgentComparisonRequest, AgentComparisonResponse,
    AlertAckResponse,
)
from engine import (
    init_db, create_agent, list_agents, get_agent, update_agent, delete_agent,
    record_usage, reset_budget, get_spend_stats, list_alerts, list_usage,
    get_dashboard, get_daily_spend,
    forecast_budget, provider_breakdown, export_usage_csv, adjust_budget,
    get_budget_history, get_tag_analytics, get_spend_anomalies,
    set_rate_limit, get_rate_limit, check_rate_limit,
    compare_agents, acknowledge_alert,
)

DB_PATH = "agentcap.db"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiosqlite.connect(DB_PATH) as db:
        await init_db()
    yield


app = FastAPI(
    title="AgentCap",
    description=(
        "AI agent spend governance: monitor, alert and cap costs. "
        "Per-model budgets, webhook alerts, cross-agent dashboard, "
        "budget forecasting, cost tags, spend anomaly detection, "
        "rate limiting, usage comparison, alert acknowledgment."
    ),
    version="1.5.0",
    lifespan=lifespan,
)


async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db


# -- Dashboard -----------------------------------------------------------------

@app.get("/dashboard", response_model=DashboardResponse)
async def dashboard(db=Depends(get_db)):
    return await get_dashboard(db)


# -- Agents --------------------------------------------------------------------

@app.post("/agents", response_model=AgentResponse, status_code=201)
async def register_agent(body: AgentCreate, db=Depends(get_db)):
    try:
        return await create_agent(db, body.model_dump())
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "Agent name already registered")
        raise


@app.get("/agents", response_model=list[AgentResponse])
async def get_agents(
    tag: Optional[str] = Query(None, description="Filter by cost tag"),
    db=Depends(get_db),
):
    return await list_agents(db, tag=tag)


@app.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent_by_id(agent_id: int, db=Depends(get_db)):
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


@app.patch("/agents/{agent_id}", response_model=AgentResponse)
async def patch_agent(agent_id: int, body: AgentUpdate, db=Depends(get_db)):
    result = await update_agent(db, agent_id, body.model_dump(exclude_unset=True))
    if not result:
        raise HTTPException(404, "Agent not found")
    return result


@app.delete("/agents/{agent_id}", status_code=204)
async def remove_agent(agent_id: int, db=Depends(get_db)):
    ok = await delete_agent(db, agent_id)
    if not ok:
        raise HTTPException(404, "Agent not found")


# -- Usage ---------------------------------------------------------------------

@app.post("/agents/{agent_id}/usage", response_model=UsageResponse, status_code=201)
async def log_usage(agent_id: int, body: UsageRecord, db=Depends(get_db)):
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent["status"] == "capped":
        raise HTTPException(429, f"Agent {agent['name']} is capped. Reset to continue.")
    # v1.5.0: check rate limits before recording
    rl_check = await check_rate_limit(db, agent_id)
    if rl_check and rl_check["throttled"]:
        raise HTTPException(429, f"Rate limit exceeded: {rl_check['reason']}")
    return await record_usage(db, agent_id, body.tokens_in, body.tokens_out, body.cost_usd, body.request_id, body.metadata)


@app.get("/agents/{agent_id}/usage/daily", response_model=list[DailySpendEntry])
async def daily_spend(agent_id: int, days: int = Query(30, ge=1, le=365), db=Depends(get_db)):
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return await get_daily_spend(db, agent_id, days)


@app.get("/agents/{agent_id}/usage/export/csv")
async def export_csv(agent_id: int, db=Depends(get_db)):
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    csv_data = await export_usage_csv(db, agent_id)
    return StreamingResponse(
        iter([csv_data]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=usage_agent{agent_id}.csv"},
    )


@app.get("/agents/{agent_id}/usage", response_model=list[UsageDetail])
async def get_agent_usage(agent_id: int, limit: int = Query(100, ge=1, le=1000), db=Depends(get_db)):
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return await list_usage(db, agent_id, limit)


# -- Budget & Forecast ---------------------------------------------------------

@app.post("/agents/{agent_id}/reset")
async def reset_agent_budget(agent_id: int, db=Depends(get_db)):
    ok = await reset_budget(db, agent_id)
    if not ok:
        raise HTTPException(404, "Agent not found")
    return {"status": "reset", "agent_id": agent_id}


@app.get("/agents/{agent_id}/forecast", response_model=ForecastResponse)
async def agent_forecast(agent_id: int, db=Depends(get_db)):
    result = await forecast_budget(db, agent_id)
    if result is None:
        raise HTTPException(404, "Agent not found")
    return result


@app.post("/agents/{agent_id}/budget/adjust", response_model=BudgetAdjustmentResponse)
async def budget_adjust(agent_id: int, body: BudgetAdjustment, db=Depends(get_db)):
    result = await adjust_budget(db, agent_id, body.new_budget_usd, body.reason)
    if result is None:
        raise HTTPException(404, "Agent not found")
    return result


@app.get("/agents/{agent_id}/budget/history", response_model=list[BudgetHistoryEntry])
async def budget_history(agent_id: int, db=Depends(get_db)):
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return await get_budget_history(db, agent_id)


@app.get("/agents/{agent_id}/stats", response_model=SpendStats)
async def agent_stats(agent_id: int, db=Depends(get_db)):
    stats = await get_spend_stats(db, agent_id)
    if not stats:
        raise HTTPException(404, "Agent not found")
    return stats


@app.get("/agents/{agent_id}/anomalies", response_model=AnomalyResponse)
async def agent_anomalies(
    agent_id: int,
    days: int = Query(30, ge=7, le=90),
    threshold: float = Query(2.0, ge=1.5, le=5.0, description="Ratio above avg to flag as anomaly"),
    db=Depends(get_db),
):
    result = await get_spend_anomalies(db, agent_id, days=days, threshold=threshold)
    if result is None:
        raise HTTPException(404, "Agent not found")
    return result


# -- Rate Limits (v1.5.0) -----------------------------------------------------

@app.put("/agents/{agent_id}/rate-limit", response_model=RateLimitResponse)
async def upsert_rate_limit(agent_id: int, body: RateLimitCreate, db=Depends(get_db)):
    result = await set_rate_limit(db, agent_id, body.requests_per_minute, body.tokens_per_hour)
    if result is None:
        raise HTTPException(404, "Agent not found")
    return result


@app.get("/agents/{agent_id}/rate-limit", response_model=RateLimitResponse)
async def read_rate_limit(agent_id: int, db=Depends(get_db)):
    result = await get_rate_limit(db, agent_id)
    if result is None:
        raise HTTPException(404, "Agent not found")
    return result


# -- Usage Comparison (v1.5.0) ------------------------------------------------

@app.post("/analytics/compare", response_model=AgentComparisonResponse)
async def compare_agents_endpoint(body: AgentComparisonRequest, db=Depends(get_db)):
    result = await compare_agents(db, body.agent_ids, body.days)
    if result is None:
        raise HTTPException(404, "One or more agents not found")
    return result


# -- Analytics -----------------------------------------------------------------

@app.get("/analytics/providers", response_model=ProviderBreakdownResponse)
async def providers_analytics(db=Depends(get_db)):
    return await provider_breakdown(db)


@app.get("/analytics/by-tag", response_model=TagAnalyticsResponse)
async def tag_analytics(db=Depends(get_db)):
    return await get_tag_analytics(db)


# -- Alerts --------------------------------------------------------------------

@app.get("/alerts", response_model=list[BudgetAlert])
async def get_all_alerts(
    alert_type: Optional[str] = Query(None, description="Filter: warning, capped, reset, budget_increased, budget_decreased"),
    acknowledged: Optional[bool] = Query(None, description="Filter by acknowledgment status"),
    db=Depends(get_db),
):
    alerts = await list_alerts(db)
    if alert_type:
        alerts = [a for a in alerts if a["alert_type"] == alert_type]
    if acknowledged is not None:
        alerts = [a for a in alerts if a.get("acknowledged", False) == acknowledged]
    return alerts


@app.get("/agents/{agent_id}/alerts", response_model=list[BudgetAlert])
async def get_agent_alerts(agent_id: int, db=Depends(get_db)):
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return await list_alerts(db, agent_id=agent_id)


@app.post("/alerts/{alert_id}/acknowledge", response_model=AlertAckResponse)
async def ack_alert(alert_id: int, db=Depends(get_db)):
    result = await acknowledge_alert(db, alert_id)
    if result is None:
        raise HTTPException(404, "Alert not found")
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.5.0"}
