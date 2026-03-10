from pydantic import BaseModel
from typing import Optional


class AgentCreate(BaseModel):
    name: str
    provider: str  # openai | anthropic | cohere | custom
    model: str     # e.g. gpt-4o, claude-3-5-sonnet
    monthly_budget_usd: float
    alert_threshold_pct: float = 80.0
    webhook_url: Optional[str] = None


class AgentUpdate(BaseModel):
    monthly_budget_usd: Optional[float] = None
    alert_threshold_pct: Optional[float] = None
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
    status: str  # ok | warning | capped
    created_at: str


class UsageRecord(BaseModel):
    agent_id: int
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float
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
    alert_type: str  # warning | capped | reset
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
