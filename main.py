from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
import aiosqlite
from models import AgentCreate, AgentResponse, UsageRecord, UsageResponse, BudgetAlert, SpendStats
from engine import init_db, create_agent, list_agents, get_agent, record_usage, reset_budget, get_spend_stats, list_alerts

DB_PATH = "agentcap.db"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiosqlite.connect(DB_PATH) as db:
        await init_db()
    yield


app = FastAPI(
    title="AgentCap",
    description="AI agent spend governance — monitor, alert and cap costs when autonomous agents overbill. Set per-model budgets and get webhook alerts before bills explode.",
    version="1.0.0",
    lifespan=lifespan,
)


async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db


@app.post("/agents", response_model=AgentResponse, status_code=201)
async def register_agent(body: AgentCreate, db=Depends(get_db)):
    """Register an AI agent with a monthly spend budget."""
    try:
        return await create_agent(db, body.model_dump())
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "Agent name already registered")
        raise


@app.get("/agents", response_model=list[AgentResponse])
async def get_agents(db=Depends(get_db)):
    """List all registered agents with current spend and status."""
    return await list_agents(db)


@app.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent_by_id(agent_id: int, db=Depends(get_db)):
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


@app.post("/agents/{agent_id}/usage", response_model=UsageResponse, status_code=201)
async def log_usage(agent_id: int, body: UsageRecord, db=Depends(get_db)):
    """Record a usage event (tokens + cost). Auto-fires alerts at threshold."""
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent["status"] == "capped":
        raise HTTPException(429, f"Agent {agent['name']} is capped — budget exhausted. Reset to continue.")
    return await record_usage(db, agent_id, body.tokens_in, body.tokens_out, body.cost_usd, body.request_id, body.metadata)


@app.post("/agents/{agent_id}/reset")
async def reset_agent_budget(agent_id: int, db=Depends(get_db)):
    """Reset monthly spend counter (use at billing cycle start)."""
    ok = await reset_budget(db, agent_id)
    if not ok:
        raise HTTPException(404, "Agent not found")
    return {"status": "reset", "agent_id": agent_id}


@app.get("/agents/{agent_id}/stats", response_model=SpendStats)
async def agent_stats(agent_id: int, db=Depends(get_db)):
    """Aggregated spend stats: total tokens, avg cost per request, spend %."""
    stats = await get_spend_stats(db, agent_id)
    if not stats:
        raise HTTPException(404, "Agent not found")
    return stats


@app.get("/alerts", response_model=list[BudgetAlert])
async def get_all_alerts(db=Depends(get_db)):
    """All budget alerts across all agents."""
    return await list_alerts(db)


@app.get("/agents/{agent_id}/alerts", response_model=list[BudgetAlert])
async def get_agent_alerts(agent_id: int, db=Depends(get_db)):
    """Budget alerts for a specific agent."""
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return await list_alerts(db, agent_id=agent_id)
