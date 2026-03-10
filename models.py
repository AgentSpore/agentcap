from pydantic import BaseModel, Field
from typing import Optional


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    provider: str = Field(..., description="openai | anthropic | cohere | custom")
    model: str
    monthly_budget_usd: float = Field(..., gt=0)
    alert_threshold_pct: float = Field(80.0, ge=1, le=100)
    webhook_url: Optional[str] = None


class AgentUpdate(BaseModel):
    monthly_budget_usd: Optional[float] = Field(None, gt=0)
    alert_threshold_pct: Optional[float] = Field(None, ge=1, le=100)
    webhook_url: Optional[str] = None


class AgentResponse(BaseModel):
    id: int
    name: str
    provider: str
    model: str
    monthly_budget_usd: float
    alert_threshold_pct: float
    webhook_url: Optional[str]
    current_spend_usd: float
    spend_pct: float
    status: str
    created_at: str


class UsageRecord(BaseModel):
    agent_id: int
    tokens_in: int = Field(0, ge=0)
    tokens_out: int = Field(0, ge=0)
    cost_usd: float = Field(..., ge=0)
    request_id: Optional[str] = None
    metadata: Optional[dict] = None


class UsageResponse(BaseModel):
    id: int
    agent_id: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    request_id: Optional[str]
    recorded_at: str


class UsageDetail(BaseModel):
    id: int
    agent_id: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    request_id: Optional[str]
    metadata: Optional[dict]
    recorded_at: str


class BudgetAlert(BaseModel):
    id: int
    agent_id: int
    agent_name: str
    alert_type: str
    spend_usd: float
    budget_usd: float
    spend_pct: float
    webhook_fired: bool
    created_at: str


class SpendStats(BaseModel):
    agent_id: int
    agent_name: str
    model: str
    total_spend_usd: float
    monthly_budget_usd: float
    spend_pct: float
    tokens_in_total: int
    tokens_out_total: int
    request_count: int
    avg_cost_per_request: float
    status: str


class DailySpendEntry(BaseModel):
    day: str
    requests: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


class TopSpender(BaseModel):
    agent_id: int
    name: str
    model: str
    spend_usd: float
    budget_usd: float
    spend_pct: float
    status: str


class DashboardResponse(BaseModel):
    total_agents: int
    total_budget_usd: float
    total_spend_usd: float
    overall_utilization_pct: float
    agents_ok: int
    agents_warning: int
    agents_capped: int
    total_requests: int
    top_spenders: list[TopSpender]
