from pydantic import BaseModel, Field
from typing import Optional


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    provider: str = Field(..., description="openai | anthropic | cohere | custom")
    model: str
    monthly_budget_usd: float = Field(..., gt=0)
    alert_threshold_pct: float = Field(80.0, ge=1, le=100)
    webhook_url: Optional[str] = None
    tags: list[str] = Field(default_factory=list, max_length=10, description="Cost allocation tags (project, team, etc.)")


class AgentUpdate(BaseModel):
    monthly_budget_usd: Optional[float] = Field(None, gt=0)
    alert_threshold_pct: Optional[float] = Field(None, ge=1, le=100)
    webhook_url: Optional[str] = None
    tags: Optional[list[str]] = Field(None, max_length=10)


class AgentResponse(BaseModel):
    id: int
    name: str
    provider: str
    model: str
    monthly_budget_usd: float
    alert_threshold_pct: float
    webhook_url: Optional[str]
    tags: list[str]
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


# -- v1.3.0: Forecast & Analytics ----------------------------------------------

class ForecastResponse(BaseModel):
    agent_id: int
    agent_name: str
    current_spend_usd: float
    monthly_budget_usd: float
    spend_pct: float
    daily_burn_rate: float
    projected_monthly_spend: float
    days_until_cap: Optional[int] = None
    projected_cap_date: Optional[str] = None
    trend: str
    recommendation: str


class ProviderStats(BaseModel):
    provider: str
    agent_count: int
    total_spend_usd: float
    total_budget_usd: float
    avg_spend_per_agent: float
    utilization_pct: float
    models: list[str]


class ProviderBreakdownResponse(BaseModel):
    providers: list[ProviderStats]
    total_providers: int
    highest_spend_provider: Optional[str]
    most_efficient_provider: Optional[str]


class BudgetAdjustment(BaseModel):
    new_budget_usd: float = Field(..., gt=0)
    reason: str = Field(..., min_length=3, max_length=500)


class BudgetAdjustmentResponse(BaseModel):
    agent_id: int
    agent_name: str
    old_budget_usd: float
    new_budget_usd: float
    reason: str
    adjusted_at: str
    new_spend_pct: float
    new_status: str


# -- v1.4.0: Tags, Budget History, Anomalies ----------------------------------

class BudgetHistoryEntry(BaseModel):
    id: int
    old_budget_usd: float
    new_budget_usd: float
    reason: str
    adjusted_at: str


class TagSpendEntry(BaseModel):
    tag: str
    agent_count: int
    total_spend_usd: float
    total_budget_usd: float
    utilization_pct: float


class TagAnalyticsResponse(BaseModel):
    tags: list[TagSpendEntry]
    total_tags: int


class AnomalyEntry(BaseModel):
    day: str
    cost_usd: float
    avg_cost_usd: float
    deviation_ratio: float
    severity: str


class AnomalyResponse(BaseModel):
    agent_id: int
    agent_name: str
    anomalies: list[AnomalyEntry]
    total_anomalies: int
    baseline_avg_daily: float
