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
    daily_quota_usd: Optional[float] = None
    daily_spend_usd: float = 0.0
    daily_quota_pct: float = 0.0
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
    policy_warnings: list[str] = Field(default_factory=list, description="Warnings from cost policy checks")


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
    acknowledged: bool = False
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
    total_groups: int
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


# -- v1.5.0: Rate Limits, Usage Comparison, Alert Ack -------------------------

class RateLimitCreate(BaseModel):
    requests_per_minute: int = Field(..., ge=1, le=10000, description="Max requests per minute")
    tokens_per_hour: int = Field(..., ge=1, le=100_000_000, description="Max tokens (in+out) per hour")


class RateLimitResponse(BaseModel):
    agent_id: int
    agent_name: str
    requests_per_minute: int
    tokens_per_hour: int
    current_rpm: int
    current_tph: int
    rpm_utilization_pct: float
    tph_utilization_pct: float
    is_throttled: bool
    updated_at: str


class AgentComparisonRequest(BaseModel):
    agent_ids: list[int] = Field(..., min_length=2, max_length=10, description="2-10 agent IDs to compare")
    days: int = Field(30, ge=1, le=365)


class AgentComparisonEntry(BaseModel):
    agent_id: int
    agent_name: str
    model: str
    provider: str
    total_spend_usd: float
    monthly_budget_usd: float
    spend_pct: float
    request_count: int
    avg_cost_per_request: float
    tokens_in_total: int
    tokens_out_total: int
    daily_avg_spend: float
    status: str


class AgentComparisonResponse(BaseModel):
    agents: list[AgentComparisonEntry]
    period_days: int
    cheapest_agent_id: int
    most_active_agent_id: int
    highest_spend_agent_id: int
    total_combined_spend: float


class AlertAckResponse(BaseModel):
    id: int
    agent_id: int
    alert_type: str
    acknowledged: bool
    acknowledged_at: str


# -- v1.6.0: Agent Groups, Daily Quotas, Cost Reports -------------------------

class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    budget_usd: Optional[float] = Field(None, ge=0, description="Optional group budget cap")


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    budget_usd: Optional[float] = Field(None, ge=0)


class GroupMemberEntry(BaseModel):
    agent_id: int
    agent_name: str
    model: str
    current_spend_usd: float
    monthly_budget_usd: float
    status: str


class GroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    budget_usd: Optional[float]
    member_count: int
    total_spend_usd: float
    total_budget_usd: float
    utilization_pct: float
    members: list[GroupMemberEntry]
    created_at: str


class GroupAddAgent(BaseModel):
    agent_id: int


class DailyQuotaSet(BaseModel):
    daily_quota_usd: float = Field(..., gt=0, description="Maximum daily spend in USD")


class DailyQuotaResponse(BaseModel):
    agent_id: int
    agent_name: str
    daily_quota_usd: float
    today_spend_usd: float
    today_pct: float
    remaining_usd: float
    is_over_quota: bool


class CostReportEntry(BaseModel):
    dimension: str
    value: str
    agent_count: int
    total_spend_usd: float
    total_budget_usd: float
    utilization_pct: float
    request_count: int


class CostReportResponse(BaseModel):
    period_days: int
    by_tag: list[CostReportEntry]
    by_provider: list[CostReportEntry]
    by_model: list[CostReportEntry]
    total_spend_usd: float
    total_budget_usd: float
    overall_utilization_pct: float


# -- v1.7.0: Agent Cloning, Hourly Usage, Batch Status ------------------------

class AgentCloneRequest(BaseModel):
    new_name: Optional[str] = Field(None, min_length=1, max_length=100, description="Name for cloned agent (default: '{original}-clone')")
    include_rate_limit: bool = Field(True, description="Copy rate limit settings")
    include_daily_quota: bool = Field(True, description="Copy daily quota setting")
    include_groups: bool = Field(True, description="Add clone to same groups as original")


class AgentCloneResponse(BaseModel):
    cloned_from: int
    cloned_from_name: str
    agent: AgentResponse


class HourlyUsageEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour of day (0-23)")
    requests: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    avg_cost_per_request: float


class HourlyUsageResponse(BaseModel):
    agent_id: int
    agent_name: str
    period_days: int
    hours: list[HourlyUsageEntry]
    peak_hour: int
    quietest_hour: int
    total_requests: int


class BatchStatusRequest(BaseModel):
    agent_ids: list[int] = Field(..., min_length=1, max_length=50, description="1-50 agent IDs")


class BatchStatusSummary(BaseModel):
    total: int
    ok: int
    warning: int
    capped: int
    not_found: int
    total_spend_usd: float
    total_budget_usd: float


class BatchStatusResponse(BaseModel):
    agents: list[AgentResponse]
    summary: BatchStatusSummary


# -- v1.8.0: Cost Policies, Spend Snapshots, Agent Activity Log ---------------

# --- Cost Policies ---

class CostPolicyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    policy_type: str = Field(..., description="max_cost_per_request | max_tokens_per_request | blocked_model | blocked_provider | max_daily_spend_per_agent")
    threshold: float = Field(..., ge=0, description="Threshold value (cost in USD, token count, or 0 for blocked model/provider)")
    action: str = Field("warn", description="warn | block")


class CostPolicyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    threshold: Optional[float] = Field(None, ge=0)
    action: Optional[str] = Field(None, description="warn | block")
    is_enabled: Optional[bool] = None


class CostPolicyResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    policy_type: str
    threshold: float
    action: str
    is_enabled: bool
    times_triggered: int
    last_triggered_at: Optional[str]
    created_at: str


class PolicyViolation(BaseModel):
    policy_id: int
    policy_name: str
    policy_type: str
    action: str
    threshold: float
    actual_value: float
    message: str


class PolicyCheckResult(BaseModel):
    violations: list[PolicyViolation]
    blocked: bool
    warnings: list[str]


class PolicyCheckRequest(BaseModel):
    agent_id: int
    cost_usd: float = Field(..., ge=0)
    tokens_in: int = Field(0, ge=0)
    tokens_out: int = Field(0, ge=0)
    model: Optional[str] = None
    provider: Optional[str] = None


class PolicyStatsResponse(BaseModel):
    total: int
    by_type: dict[str, int]
    most_triggered: list[CostPolicyResponse]


# --- Spend Snapshots ---

class SnapshotCreate(BaseModel):
    snapshot_type: str = Field("manual", description="daily | weekly | monthly | manual")


class SnapshotResponse(BaseModel):
    id: int
    snapshot_type: str
    total_agents: int
    active_agents: int
    total_budget_usd: float
    total_spend_usd: float
    utilization_pct: float
    total_alerts: int
    unacknowledged_alerts: int
    top_spender_id: Optional[str]
    top_spender_name: Optional[str]
    top_spender_spend: float
    groups_count: int
    avg_agent_spend: float
    created_at: str


class SnapshotTrend(BaseModel):
    snapshots: list[SnapshotResponse]
    spend_trend: str = Field(..., description="increasing | stable | decreasing")
    avg_utilization: float


# --- Agent Activity Log ---

class ActivityEntry(BaseModel):
    id: int
    agent_id: str
    agent_name: str
    action: str
    category: str
    details: dict
    performed_at: str


class ActivityFilter(BaseModel):
    agent_id: Optional[str] = None
    category: Optional[str] = None
    action: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    limit: int = Field(50, ge=1, le=1000)


class ActivityStatsResponse(BaseModel):
    total: int
    by_category: dict[str, int]
    by_action: dict[str, int]
    most_active_agents: list[dict]


# -- v1.9.0: Cost Optimizations, Cost Centers / Chargebacks, Notification Channels

# --- Cost Optimization Suggestions ---

class OptimizationSuggestion(BaseModel):
    type: str = Field(..., description="underutilized | budget_right_size | cost_spike | cheaper_model")
    severity: str = Field(..., description="low | medium | high")
    agent_id: int
    agent_name: str
    estimated_savings_usd: float
    description: str
    details: dict = Field(default_factory=dict)


class OptimizationResponse(BaseModel):
    agent_id: int
    agent_name: str
    suggestions: list[OptimizationSuggestion]
    total_suggestions: int
    total_potential_savings_usd: float


class OptimizationSummaryByType(BaseModel):
    type: str
    count: int
    total_savings_usd: float


class OptimizationSummaryAgent(BaseModel):
    agent_id: int
    agent_name: str
    suggestion_count: int
    total_savings_usd: float


class OptimizationSummaryResponse(BaseModel):
    total_suggestions: int
    total_potential_savings_usd: float
    by_type: list[OptimizationSummaryByType]
    top_agents: list[OptimizationSummaryAgent]


# --- Cost Centers / Chargebacks ---

class CostCenterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    owner: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=500)


class CostCenterUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    owner: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=500)


class CostCenterAgentEntry(BaseModel):
    agent_id: int
    agent_name: str
    allocation_pct: float
    current_spend_usd: float
    allocated_spend_usd: float


class CostCenterResponse(BaseModel):
    id: int
    name: str
    owner: str
    description: Optional[str]
    agents: list[CostCenterAgentEntry]
    total_allocated_spend_usd: float
    agent_count: int
    created_at: str


class CostCenterAddAgent(BaseModel):
    agent_id: int
    allocation_pct: float = Field(..., gt=0, le=100, description="Percentage of agent cost allocated to this center (1-100)")


class ChargebackAgentEntry(BaseModel):
    agent_id: int
    agent_name: str
    allocation_pct: float
    total_spend_usd: float
    allocated_cost_usd: float


class ChargebackResponse(BaseModel):
    cost_center_id: int
    cost_center_name: str
    owner: str
    period_days: int
    agents: list[ChargebackAgentEntry]
    total_cost_center_spend_usd: float
    generated_at: str


# --- Notification Channels ---

class NotificationChannelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    channel_type: str = Field(..., description="email | slack | webhook")
    config: dict = Field(..., description="Channel-specific config (e.g. email address, slack webhook URL, webhook URL)")


class NotificationChannelUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    channel_type: Optional[str] = Field(None, description="email | slack | webhook")
    config: Optional[dict] = None


class NotificationChannelResponse(BaseModel):
    id: int
    name: str
    channel_type: str
    config: dict
    is_active: bool
    created_at: str
    updated_at: str


class AgentNotificationSubscription(BaseModel):
    channel_id: int


class AgentChannelEntry(BaseModel):
    channel_id: int
    channel_name: str
    channel_type: str
    subscribed_at: str


class TestNotificationResponse(BaseModel):
    channel_id: int
    channel_name: str
    channel_type: str
    status: str
    message: str


# -- v2.0.0: API Key Management, SLA Monitoring, Compliance Reports -----------

# --- API Key Management ---

class ApiKeyCreate(BaseModel):
    agent_id: int
    name: str = "default"
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=365)


class ApiKeyResponse(BaseModel):
    id: int
    agent_id: int
    name: str
    key_prefix: str
    status: str  # active, expired, revoked
    requests_count: int
    last_used_at: Optional[str]
    expires_at: Optional[str]
    created_at: str


class ApiKeyCreatedResponse(BaseModel):
    id: int
    agent_id: int
    name: str
    api_key: str  # full key shown only once
    key_prefix: str
    expires_at: Optional[str]
    created_at: str


# --- SLA Monitoring ---

class SlaConfigCreate(BaseModel):
    agent_id: int
    max_response_ms: int = Field(default=5000, ge=100, le=60000)
    min_availability_pct: float = Field(default=99.0, ge=0, le=100)
    evaluation_window_hours: int = Field(default=24, ge=1, le=720)


class SlaConfigResponse(BaseModel):
    id: int
    agent_id: int
    max_response_ms: int
    min_availability_pct: float
    evaluation_window_hours: int
    created_at: str
    updated_at: str


class SlaMetricRecord(BaseModel):
    agent_id: int
    response_ms: int = Field(ge=0)
    success: bool = True


class SlaStatusResponse(BaseModel):
    agent_id: int
    current_availability_pct: float
    avg_response_ms: float
    p95_response_ms: float
    p99_response_ms: float
    total_requests: int
    failed_requests: int
    sla_compliant: bool
    breaches: int
    evaluation_window_hours: int


class SlaBreach(BaseModel):
    id: int
    agent_id: int
    breach_type: str  # response_time, availability
    threshold: float
    actual_value: float
    created_at: str


# --- Compliance Reports ---

class ComplianceReportRequest(BaseModel):
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    agent_ids: Optional[list[int]] = None


class CompliancePolicyViolation(BaseModel):
    id: int
    agent_id: int
    policy_id: int
    policy_name: str
    violation_type: str
    details: str
    created_at: str


class ComplianceScore(BaseModel):
    agent_id: int
    agent_name: str
    total_requests: int
    violations: int
    compliance_pct: float
    risk_level: str  # low, medium, high


class ComplianceReport(BaseModel):
    period_start: str
    period_end: str
    total_agents: int
    total_requests: int
    total_violations: int
    overall_compliance_pct: float
    agent_scores: list[ComplianceScore]
    top_violations: list[dict]
    recommendations: list[str]
