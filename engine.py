import aiosqlite
import csv
import io
import json
import httpx
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = "agentcap.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                monthly_budget_usd REAL NOT NULL,
                alert_threshold_pct REAL NOT NULL DEFAULT 80.0,
                webhook_url TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                current_spend_usd REAL NOT NULL DEFAULT 0.0,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                tokens_in INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL,
                request_id TEXT,
                metadata TEXT,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                spend_usd REAL NOT NULL,
                budget_usd REAL NOT NULL,
                spend_pct REAL NOT NULL,
                webhook_fired INTEGER NOT NULL DEFAULT 0,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                acknowledged_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS budget_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                old_budget_usd REAL NOT NULL,
                new_budget_usd REAL NOT NULL,
                reason TEXT NOT NULL,
                adjusted_at TEXT NOT NULL,
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        # v1.5.0: rate limits table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                agent_id INTEGER PRIMARY KEY,
                requests_per_minute INTEGER NOT NULL DEFAULT 60,
                tokens_per_hour INTEGER NOT NULL DEFAULT 1000000,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        # v1.6.0: agent groups
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                budget_usd REAL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_group_members (
                group_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (group_id, agent_id),
                FOREIGN KEY (group_id) REFERENCES agent_groups(id),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        # v1.8.0: cost policies
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cost_policies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                policy_type TEXT NOT NULL,
                threshold REAL NOT NULL,
                action TEXT NOT NULL DEFAULT 'warn',
                is_enabled INTEGER NOT NULL DEFAULT 1,
                times_triggered INTEGER NOT NULL DEFAULT 0,
                last_triggered_at TEXT,
                created_at TEXT NOT NULL
            )
        """)
        # v1.8.0: spend snapshots
        await db.execute("""
            CREATE TABLE IF NOT EXISTS spend_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_type TEXT NOT NULL DEFAULT 'manual',
                total_agents INTEGER NOT NULL DEFAULT 0,
                active_agents INTEGER NOT NULL DEFAULT 0,
                total_budget_usd REAL NOT NULL DEFAULT 0.0,
                total_spend_usd REAL NOT NULL DEFAULT 0.0,
                utilization_pct REAL NOT NULL DEFAULT 0.0,
                total_alerts INTEGER NOT NULL DEFAULT 0,
                unacknowledged_alerts INTEGER NOT NULL DEFAULT 0,
                top_spender_id TEXT,
                top_spender_name TEXT,
                top_spender_spend REAL NOT NULL DEFAULT 0.0,
                groups_count INTEGER NOT NULL DEFAULT 0,
                avg_agent_spend REAL NOT NULL DEFAULT 0.0,
                created_at TEXT NOT NULL
            )
        """)
        # v1.8.0: agent activity log
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                action TEXT NOT NULL,
                category TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                performed_at TEXT NOT NULL
            )
        """)
        # v1.9.0: cost centers
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cost_centers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL
            )
        """)
        # v1.9.0: cost center agent allocations
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cost_center_agents (
                cost_center_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                allocation_pct REAL NOT NULL DEFAULT 100.0,
                added_at TEXT NOT NULL,
                PRIMARY KEY (cost_center_id, agent_id),
                FOREIGN KEY (cost_center_id) REFERENCES cost_centers(id),
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        # v1.9.0: notification channels
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notification_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                channel_type TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # v1.9.0: agent notification channel subscriptions
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_notification_subscriptions (
                agent_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                subscribed_at TEXT NOT NULL,
                PRIMARY KEY (agent_id, channel_id),
                FOREIGN KEY (agent_id) REFERENCES agents(id),
                FOREIGN KEY (channel_id) REFERENCES notification_channels(id)
            )
        """)
        # v1.4.0: ensure tags column
        try:
            await db.execute("SELECT tags FROM agents LIMIT 1")
        except Exception:
            await db.execute("ALTER TABLE agents ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
        # v1.5.0: ensure acknowledged columns on alerts
        try:
            await db.execute("SELECT acknowledged FROM alerts LIMIT 1")
        except Exception:
            await db.execute("ALTER TABLE alerts ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0")
            await db.execute("ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT")
        # v1.6.0: daily quota on agents
        try:
            await db.execute("SELECT daily_quota_usd FROM agents LIMIT 1")
        except Exception:
            await db.execute("ALTER TABLE agents ADD COLUMN daily_quota_usd REAL")
        await db.commit()


async def _get_daily_spend_today(db, agent_id: int) -> float:
    """Get total spend for an agent today."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    row = await (await db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE agent_id=? AND date(recorded_at)=?",
        (agent_id, today),
    )).fetchone()
    return row[0] if row else 0.0


def _agent_row(r) -> dict:
    spend = r["current_spend_usd"]
    budget = r["monthly_budget_usd"]
    pct = round((spend / budget) * 100, 1) if budget > 0 else 0
    if pct >= 100:
        status = "capped"
    elif pct >= r["alert_threshold_pct"]:
        status = "warning"
    else:
        status = "ok"
    tags_raw = r["tags"] if "tags" in r.keys() else "[]"
    daily_quota = None
    try:
        daily_quota = r["daily_quota_usd"]
    except (IndexError, KeyError):
        pass
    return {
        "id": r["id"], "name": r["name"], "provider": r["provider"], "model": r["model"],
        "monthly_budget_usd": r["monthly_budget_usd"],
        "alert_threshold_pct": r["alert_threshold_pct"],
        "webhook_url": r["webhook_url"],
        "tags": json.loads(tags_raw) if tags_raw else [],
        "current_spend_usd": spend,
        "spend_pct": pct,
        "daily_quota_usd": daily_quota,
        "daily_spend_usd": 0.0,
        "daily_quota_pct": 0.0,
        "status": status,
        "created_at": r["created_at"],
    }


async def _agent_row_with_daily(db, r) -> dict:
    """Build agent row with daily spend info populated."""
    data = _agent_row(r)
    daily_spend = await _get_daily_spend_today(db, data["id"])
    data["daily_spend_usd"] = round(daily_spend, 6)
    if data["daily_quota_usd"] and data["daily_quota_usd"] > 0:
        data["daily_quota_pct"] = round((daily_spend / data["daily_quota_usd"]) * 100, 1)
    return data


# -- v1.8.0: Agent Activity Log helpers ----------------------------------------

async def log_agent_activity(db, agent_id, agent_name: str, action: str, category: str, details: dict | None = None):
    """Record an activity entry in the agent activity log."""
    now = datetime.utcnow().isoformat()
    await db.execute(
        """INSERT INTO agent_activity (agent_id, agent_name, action, category, details, performed_at)
           VALUES (?,?,?,?,?,?)""",
        (str(agent_id), agent_name, action, category, json.dumps(details or {}), now),
    )
    await db.commit()


async def list_agent_activity(db, agent_id: str | None = None, category: str | None = None,
                               action: str | None = None, since: str | None = None,
                               until: str | None = None, limit: int = 50) -> list[dict]:
    """List activity entries with optional filters."""
    db.row_factory = aiosqlite.Row
    conditions = []
    params = []
    if agent_id is not None:
        conditions.append("agent_id = ?")
        params.append(str(agent_id))
    if category is not None:
        conditions.append("category = ?")
        params.append(category)
    if action is not None:
        conditions.append("action = ?")
        params.append(action)
    if since is not None:
        conditions.append("performed_at >= ?")
        params.append(since)
    if until is not None:
        conditions.append("performed_at <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    rows = await (await db.execute(
        f"SELECT * FROM agent_activity {where} ORDER BY id DESC LIMIT ?", params,
    )).fetchall()
    return [
        {
            "id": r["id"],
            "agent_id": r["agent_id"],
            "agent_name": r["agent_name"],
            "action": r["action"],
            "category": r["category"],
            "details": json.loads(r["details"]) if r["details"] else {},
            "performed_at": r["performed_at"],
        }
        for r in rows
    ]


async def get_activity_stats(db) -> dict:
    """Get activity statistics: total, by_category, by_action, most_active_agents."""
    db.row_factory = aiosqlite.Row
    total_row = await (await db.execute("SELECT COUNT(*) FROM agent_activity")).fetchone()
    total = total_row[0] if total_row else 0

    # By category
    cat_rows = await (await db.execute(
        "SELECT category, COUNT(*) as cnt FROM agent_activity GROUP BY category ORDER BY cnt DESC"
    )).fetchall()
    by_category = {r["category"]: r["cnt"] for r in cat_rows}

    # By action
    act_rows = await (await db.execute(
        "SELECT action, COUNT(*) as cnt FROM agent_activity GROUP BY action ORDER BY cnt DESC"
    )).fetchall()
    by_action = {r["action"]: r["cnt"] for r in act_rows}

    # Most active agents (top 10)
    agent_rows = await (await db.execute(
        """SELECT agent_id, agent_name, COUNT(*) as cnt
           FROM agent_activity GROUP BY agent_id
           ORDER BY cnt DESC LIMIT 10"""
    )).fetchall()
    most_active_agents = [
        {"agent_id": r["agent_id"], "agent_name": r["agent_name"], "activity_count": r["cnt"]}
        for r in agent_rows
    ]

    return {
        "total": total,
        "by_category": by_category,
        "by_action": by_action,
        "most_active_agents": most_active_agents,
    }


# -- Agent CRUD (with activity logging) ----------------------------------------

async def create_agent(db, data: dict) -> dict:
    now = datetime.utcnow().isoformat()
    tags = json.dumps(data.get("tags", []))
    cur = await db.execute(
        """INSERT INTO agents (name, provider, model, monthly_budget_usd, alert_threshold_pct, webhook_url, tags, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (data["name"], data["provider"], data["model"],
         data["monthly_budget_usd"], data.get("alert_threshold_pct", 80.0),
         data.get("webhook_url"), tags, now),
    )
    await db.commit()
    async with aiosqlite.connect(DB_PATH) as db2:
        db2.row_factory = aiosqlite.Row
        r = await (await db2.execute("SELECT * FROM agents WHERE id=?", (cur.lastrowid,))).fetchone()
        agent = await _agent_row_with_daily(db2, r)
    # v1.8.0: log activity
    await log_agent_activity(db, agent["id"], agent["name"], "agent_created", "config", {
        "provider": data["provider"], "model": data["model"],
        "monthly_budget_usd": data["monthly_budget_usd"],
        "tags": data.get("tags", []),
    })
    return agent


async def list_agents(db, tag: str | None = None) -> list:
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute("SELECT * FROM agents ORDER BY id DESC")).fetchall()
    agents = []
    for r in rows:
        agents.append(await _agent_row_with_daily(db, r))
    if tag:
        agents = [a for a in agents if tag in a["tags"]]
    return agents


async def get_agent(db, agent_id: int):
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT * FROM agents WHERE id=?", (agent_id,))).fetchone()
    if not r:
        return None
    return await _agent_row_with_daily(db, r)


async def update_agent(db, agent_id: int, updates: dict) -> dict | None:
    allowed = {"monthly_budget_usd", "alert_threshold_pct", "webhook_url", "tags"}
    # v1.8.0: capture old values for activity log
    old_agent = await get_agent(db, agent_id)
    if not old_agent:
        return None
    fields = {}
    for k, v in updates.items():
        if k in allowed and v is not None:
            if k == "tags":
                fields[k] = json.dumps(v)
            else:
                fields[k] = v
    if not fields:
        return await get_agent(db, agent_id)
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [agent_id]
    cur = await db.execute(f"UPDATE agents SET {set_clause} WHERE id=?", values)
    await db.commit()
    if cur.rowcount == 0:
        return None
    updated = await get_agent(db, agent_id)
    # v1.8.0: log activity with changed fields
    changed = {}
    for k, v in updates.items():
        if k in allowed and v is not None:
            old_val = old_agent.get(k)
            if old_val != v:
                changed[k] = {"old": old_val, "new": v}
    if changed:
        await log_agent_activity(db, agent_id, updated["name"], "agent_updated", "config", {"changed_fields": changed})
    return updated


async def delete_agent(db, agent_id: int) -> bool:
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT * FROM agents WHERE id=?", (agent_id,))).fetchone()
    if not r:
        return False
    agent_name = r["name"]
    await db.execute("DELETE FROM usage WHERE agent_id=?", (agent_id,))
    await db.execute("DELETE FROM alerts WHERE agent_id=?", (agent_id,))
    await db.execute("DELETE FROM budget_adjustments WHERE agent_id=?", (agent_id,))
    await db.execute("DELETE FROM rate_limits WHERE agent_id=?", (agent_id,))
    await db.execute("DELETE FROM agent_group_members WHERE agent_id=?", (agent_id,))
    # v1.9.0: clean up cost center and notification subscriptions
    await db.execute("DELETE FROM cost_center_agents WHERE agent_id=?", (agent_id,))
    await db.execute("DELETE FROM agent_notification_subscriptions WHERE agent_id=?", (agent_id,))
    await db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
    await db.commit()
    # v1.8.0: log activity
    await log_agent_activity(db, agent_id, agent_name, "agent_deleted", "config", {"agent_id": agent_id})
    return True


# -- v1.8.0: Cost Policies ----------------------------------------------------

VALID_POLICY_TYPES = {
    "max_cost_per_request", "max_tokens_per_request",
    "blocked_model", "blocked_provider", "max_daily_spend_per_agent",
}
VALID_POLICY_ACTIONS = {"warn", "block"}


async def create_cost_policy(db, data: dict) -> dict:
    """Create a new cost policy."""
    policy_type = data.get("policy_type", "")
    if policy_type not in VALID_POLICY_TYPES:
        raise ValueError(f"Invalid policy_type: {policy_type}. Must be one of: {', '.join(sorted(VALID_POLICY_TYPES))}")
    action = data.get("action", "warn")
    if action not in VALID_POLICY_ACTIONS:
        raise ValueError(f"Invalid action: {action}. Must be one of: {', '.join(sorted(VALID_POLICY_ACTIONS))}")
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        """INSERT INTO cost_policies (name, description, policy_type, threshold, action, created_at)
           VALUES (?,?,?,?,?,?)""",
        (data["name"], data.get("description"), policy_type, data["threshold"], action, now),
    )
    await db.commit()
    return await get_cost_policy(db, cur.lastrowid)


async def list_cost_policies(db, is_enabled: bool | None = None, policy_type: str | None = None) -> list[dict]:
    """List cost policies with optional filters."""
    db.row_factory = aiosqlite.Row
    conditions = []
    params = []
    if is_enabled is not None:
        conditions.append("is_enabled = ?")
        params.append(1 if is_enabled else 0)
    if policy_type is not None:
        conditions.append("policy_type = ?")
        params.append(policy_type)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await (await db.execute(
        f"SELECT * FROM cost_policies {where} ORDER BY id DESC", params,
    )).fetchall()
    return [_policy_row(r) for r in rows]


async def get_cost_policy(db, policy_id: int) -> dict | None:
    """Get a single cost policy by ID."""
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT * FROM cost_policies WHERE id=?", (policy_id,))).fetchone()
    if not r:
        return None
    return _policy_row(r)


async def update_cost_policy(db, policy_id: int, updates: dict) -> dict | None:
    """Update an existing cost policy."""
    r = await (await db.execute("SELECT id FROM cost_policies WHERE id=?", (policy_id,))).fetchone()
    if not r:
        return None
    allowed = {"name", "description", "threshold", "action", "is_enabled"}
    fields = {}
    for k, v in updates.items():
        if k in allowed and v is not None:
            if k == "action" and v not in VALID_POLICY_ACTIONS:
                raise ValueError(f"Invalid action: {v}. Must be one of: {', '.join(sorted(VALID_POLICY_ACTIONS))}")
            if k == "is_enabled":
                fields[k] = 1 if v else 0
            else:
                fields[k] = v
    if not fields:
        return await get_cost_policy(db, policy_id)
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [policy_id]
    await db.execute(f"UPDATE cost_policies SET {set_clause} WHERE id=?", values)
    await db.commit()
    return await get_cost_policy(db, policy_id)


async def delete_cost_policy(db, policy_id: int) -> bool:
    """Delete a cost policy."""
    r = await (await db.execute("SELECT id FROM cost_policies WHERE id=?", (policy_id,))).fetchone()
    if not r:
        return False
    await db.execute("DELETE FROM cost_policies WHERE id=?", (policy_id,))
    await db.commit()
    return True


async def check_policies(db, agent_id: int, cost_usd: float, tokens_in: int, tokens_out: int,
                          model: str | None = None, provider: str | None = None) -> dict:
    """Check all enabled policies against the given usage parameters.

    Returns a PolicyCheckResult dict with violations, blocked flag, and warnings.
    """
    db.row_factory = aiosqlite.Row
    policies = await (await db.execute(
        "SELECT * FROM cost_policies WHERE is_enabled = 1"
    )).fetchall()

    # Resolve agent's model/provider if not provided
    if model is None or provider is None:
        agent = await get_agent(db, agent_id)
        if agent:
            if model is None:
                model = agent.get("model", "")
            if provider is None:
                provider = agent.get("provider", "")

    violations = []
    warnings = []
    blocked = False
    now = datetime.utcnow().isoformat()

    for p in policies:
        p_type = p["policy_type"]
        threshold = p["threshold"]
        action = p["action"]
        violation = None

        if p_type == "max_cost_per_request":
            if cost_usd > threshold:
                violation = {
                    "policy_id": p["id"],
                    "policy_name": p["name"],
                    "policy_type": p_type,
                    "action": action,
                    "threshold": threshold,
                    "actual_value": cost_usd,
                    "message": f"Request cost ${cost_usd} exceeds policy limit ${threshold}",
                }

        elif p_type == "max_tokens_per_request":
            total_tokens = tokens_in + tokens_out
            if total_tokens > threshold:
                violation = {
                    "policy_id": p["id"],
                    "policy_name": p["name"],
                    "policy_type": p_type,
                    "action": action,
                    "threshold": threshold,
                    "actual_value": float(total_tokens),
                    "message": f"Request tokens {total_tokens} exceeds policy limit {int(threshold)}",
                }

        elif p_type == "blocked_model":
            # threshold is 0 for blocked model; name contains the model name as description
            # We use the description field to store the blocked model name
            blocked_model = (p["description"] or "").strip()
            if blocked_model and model and model.lower() == blocked_model.lower():
                violation = {
                    "policy_id": p["id"],
                    "policy_name": p["name"],
                    "policy_type": p_type,
                    "action": action,
                    "threshold": threshold,
                    "actual_value": 0,
                    "message": f"Model '{model}' is blocked by policy '{p['name']}'",
                }

        elif p_type == "blocked_provider":
            blocked_prov = (p["description"] or "").strip()
            if blocked_prov and provider and provider.lower() == blocked_prov.lower():
                violation = {
                    "policy_id": p["id"],
                    "policy_name": p["name"],
                    "policy_type": p_type,
                    "action": action,
                    "threshold": threshold,
                    "actual_value": 0,
                    "message": f"Provider '{provider}' is blocked by policy '{p['name']}'",
                }

        elif p_type == "max_daily_spend_per_agent":
            today_spend = await _get_daily_spend_today(db, agent_id)
            projected = today_spend + cost_usd
            if projected > threshold:
                violation = {
                    "policy_id": p["id"],
                    "policy_name": p["name"],
                    "policy_type": p_type,
                    "action": action,
                    "threshold": threshold,
                    "actual_value": round(projected, 6),
                    "message": f"Daily spend ${round(projected, 4)} would exceed policy limit ${threshold}",
                }

        if violation:
            violations.append(violation)
            # Increment times_triggered
            await db.execute(
                "UPDATE cost_policies SET times_triggered = times_triggered + 1, last_triggered_at = ? WHERE id = ?",
                (now, p["id"]),
            )
            if action == "block":
                blocked = True
            else:
                warnings.append(violation["message"])

    if violations:
        await db.commit()

    return {
        "violations": violations,
        "blocked": blocked,
        "warnings": warnings,
    }


async def get_policy_stats(db) -> dict:
    """Get policy statistics: total, by_type, most_triggered."""
    db.row_factory = aiosqlite.Row
    total_row = await (await db.execute("SELECT COUNT(*) FROM cost_policies")).fetchone()
    total = total_row[0] if total_row else 0

    type_rows = await (await db.execute(
        "SELECT policy_type, COUNT(*) as cnt FROM cost_policies GROUP BY policy_type ORDER BY cnt DESC"
    )).fetchall()
    by_type = {r["policy_type"]: r["cnt"] for r in type_rows}

    triggered_rows = await (await db.execute(
        "SELECT * FROM cost_policies WHERE times_triggered > 0 ORDER BY times_triggered DESC LIMIT 10"
    )).fetchall()
    most_triggered = [_policy_row(r) for r in triggered_rows]

    return {
        "total": total,
        "by_type": by_type,
        "most_triggered": most_triggered,
    }


def _policy_row(r) -> dict:
    """Convert a cost_policies row to a response dict."""
    return {
        "id": r["id"],
        "name": r["name"],
        "description": r["description"],
        "policy_type": r["policy_type"],
        "threshold": r["threshold"],
        "action": r["action"],
        "is_enabled": bool(r["is_enabled"]),
        "times_triggered": r["times_triggered"],
        "last_triggered_at": r["last_triggered_at"],
        "created_at": r["created_at"],
    }


# -- Usage Recording (with policy checks) -------------------------------------

async def record_usage(db, agent_id: int, tokens_in: int, tokens_out: int,
                       cost_usd: float, request_id: str | None, metadata: dict | None) -> dict:
    now = datetime.utcnow().isoformat()
    db.row_factory = aiosqlite.Row

    # v1.6.0: check daily quota before recording
    agent_raw = await (await db.execute("SELECT * FROM agents WHERE id=?", (agent_id,))).fetchone()
    if agent_raw:
        quota = agent_raw["daily_quota_usd"] if "daily_quota_usd" in agent_raw.keys() else None
        if quota and quota > 0:
            today_spend = await _get_daily_spend_today(db, agent_id)
            if today_spend + cost_usd > quota:
                raise ValueError(f"Daily quota exceeded: ${round(today_spend + cost_usd, 4)} > ${quota} limit")

    # v1.8.0: check cost policies BEFORE recording
    agent_data = _agent_row(agent_raw) if agent_raw else None
    policy_result = await check_policies(
        db, agent_id, cost_usd, tokens_in, tokens_out,
        model=agent_data["model"] if agent_data else None,
        provider=agent_data["provider"] if agent_data else None,
    )
    if policy_result["blocked"]:
        # Return violations for the caller (main.py will raise 403)
        raise PermissionError(json.dumps({
            "detail": "Request blocked by cost policy",
            "violations": policy_result["violations"],
        }))

    cur = await db.execute(
        "INSERT INTO usage (agent_id, tokens_in, tokens_out, cost_usd, request_id, metadata, recorded_at) VALUES (?,?,?,?,?,?,?)",
        (agent_id, tokens_in, tokens_out, cost_usd, request_id, json.dumps(metadata) if metadata else None, now),
    )
    usage_id = cur.lastrowid
    await db.execute(
        "UPDATE agents SET current_spend_usd = current_spend_usd + ? WHERE id=?",
        (cost_usd, agent_id),
    )
    await db.commit()

    agent = await get_agent(db, agent_id)
    pct = agent["spend_pct"]
    threshold = agent["alert_threshold_pct"]

    if pct >= 100 or (pct >= threshold and pct - (cost_usd / agent["monthly_budget_usd"] * 100) < threshold):
        alert_type = "capped" if pct >= 100 else "warning"
        alert_cur = await db.execute(
            "INSERT INTO alerts (agent_id, agent_name, alert_type, spend_usd, budget_usd, spend_pct, created_at) VALUES (?,?,?,?,?,?,?)",
            (agent_id, agent["name"], alert_type, agent["current_spend_usd"],
             agent["monthly_budget_usd"], pct, now),
        )
        alert_id = alert_cur.lastrowid
        await db.commit()

        webhook = agent.get("webhook_url")
        if webhook:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    await client.post(webhook, json={
                        "event": alert_type, "agent": agent["name"],
                        "spend_usd": agent["current_spend_usd"],
                        "budget_usd": agent["monthly_budget_usd"],
                        "spend_pct": pct,
                    })
                await db.execute("UPDATE alerts SET webhook_fired=1 WHERE id=?", (alert_id,))
                await db.commit()
            except Exception:
                pass

    return {
        "id": usage_id, "agent_id": agent_id,
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "cost_usd": cost_usd, "request_id": request_id, "recorded_at": now,
        "policy_warnings": policy_result.get("warnings", []),
    }


async def list_usage(db, agent_id: int, limit: int = 100) -> list:
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        "SELECT * FROM usage WHERE agent_id=? ORDER BY id DESC LIMIT ?",
        (agent_id, limit),
    )).fetchall()
    return [
        {
            "id": r["id"], "agent_id": r["agent_id"],
            "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"],
            "cost_usd": r["cost_usd"], "request_id": r["request_id"],
            "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
            "recorded_at": r["recorded_at"],
        }
        for r in rows
    ]


async def get_daily_spend(db, agent_id: int, days: int = 30) -> list[dict]:
    db.row_factory = aiosqlite.Row
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = await (await db.execute(
        """SELECT date(recorded_at) as day,
                  COUNT(*) as requests,
                  SUM(tokens_in) as tin,
                  SUM(tokens_out) as tout,
                  SUM(cost_usd) as cost
           FROM usage
           WHERE agent_id=? AND date(recorded_at) >= ?
           GROUP BY date(recorded_at)
           ORDER BY day DESC""",
        (agent_id, cutoff),
    )).fetchall()
    return [
        {
            "day": r["day"],
            "requests": r["requests"],
            "tokens_in": r["tin"] or 0,
            "tokens_out": r["tout"] or 0,
            "cost_usd": round(r["cost"] or 0, 6),
        }
        for r in rows
    ]


async def reset_budget(db, agent_id: int) -> bool:
    now = datetime.utcnow().isoformat()
    cur = await db.execute("UPDATE agents SET current_spend_usd=0 WHERE id=?", (agent_id,))
    if cur.rowcount == 0:
        return False
    agent = await get_agent(db, agent_id)
    await db.execute(
        "INSERT INTO alerts (agent_id, agent_name, alert_type, spend_usd, budget_usd, spend_pct, created_at) VALUES (?,?,?,?,?,?,?)",
        (agent_id, agent["name"], "reset", 0, agent["monthly_budget_usd"], 0, now),
    )
    await db.commit()
    return True


async def get_spend_stats(db, agent_id: int) -> dict | None:
    db.row_factory = aiosqlite.Row
    agent = await get_agent(db, agent_id)
    if not agent:
        return None
    rows = await (await db.execute(
        "SELECT SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), COUNT(*) FROM usage WHERE agent_id=?",
        (agent_id,)
    )).fetchone()
    tin, tout, total_cost, count = rows[0] or 0, rows[1] or 0, rows[2] or 0.0, rows[3] or 0
    return {
        "agent_id": agent_id,
        "agent_name": agent["name"],
        "model": agent["model"],
        "total_spend_usd": round(total_cost, 6),
        "monthly_budget_usd": agent["monthly_budget_usd"],
        "spend_pct": agent["spend_pct"],
        "tokens_in_total": tin,
        "tokens_out_total": tout,
        "request_count": count,
        "avg_cost_per_request": round(total_cost / count, 6) if count else 0,
        "status": agent["status"],
    }


async def get_dashboard(db) -> dict:
    db.row_factory = aiosqlite.Row
    agents = await (await db.execute("SELECT * FROM agents ORDER BY current_spend_usd DESC")).fetchall()
    # Count groups
    grp_row = await (await db.execute("SELECT COUNT(*) FROM agent_groups")).fetchone()
    total_groups = grp_row[0] if grp_row else 0

    if not agents:
        return {
            "total_agents": 0, "total_budget_usd": 0, "total_spend_usd": 0,
            "overall_utilization_pct": 0, "agents_ok": 0, "agents_warning": 0,
            "agents_capped": 0, "total_requests": 0, "total_groups": total_groups,
            "top_spenders": [],
        }

    parsed = [_agent_row(a) for a in agents]
    total_budget = sum(a["monthly_budget_usd"] for a in parsed)
    total_spend = sum(a["current_spend_usd"] for a in parsed)
    utilization = round((total_spend / total_budget) * 100, 1) if total_budget > 0 else 0

    ok = sum(1 for a in parsed if a["status"] == "ok")
    warning = sum(1 for a in parsed if a["status"] == "warning")
    capped = sum(1 for a in parsed if a["status"] == "capped")

    req_row = await (await db.execute("SELECT COUNT(*) FROM usage")).fetchone()
    total_requests = req_row[0] if req_row else 0

    top = [
        {
            "agent_id": a["id"], "name": a["name"], "model": a["model"],
            "spend_usd": round(a["current_spend_usd"], 6),
            "budget_usd": a["monthly_budget_usd"],
            "spend_pct": a["spend_pct"], "status": a["status"],
        }
        for a in parsed[:5]
    ]

    return {
        "total_agents": len(parsed),
        "total_budget_usd": round(total_budget, 2),
        "total_spend_usd": round(total_spend, 6),
        "overall_utilization_pct": utilization,
        "agents_ok": ok,
        "agents_warning": warning,
        "agents_capped": capped,
        "total_requests": total_requests,
        "total_groups": total_groups,
        "top_spenders": top,
    }


async def list_alerts(db, agent_id: int | None = None) -> list:
    db.row_factory = aiosqlite.Row
    if agent_id:
        rows = await (await db.execute(
            "SELECT * FROM alerts WHERE agent_id=? ORDER BY id DESC", (agent_id,)
        )).fetchall()
    else:
        rows = await (await db.execute("SELECT * FROM alerts ORDER BY id DESC")).fetchall()
    results = []
    for r in rows:
        entry = {
            "id": r["id"], "agent_id": r["agent_id"], "agent_name": r["agent_name"],
            "alert_type": r["alert_type"], "spend_usd": r["spend_usd"],
            "budget_usd": r["budget_usd"], "spend_pct": r["spend_pct"],
            "webhook_fired": bool(r["webhook_fired"]), "created_at": r["created_at"],
        }
        try:
            entry["acknowledged"] = bool(r["acknowledged"])
        except (IndexError, KeyError):
            entry["acknowledged"] = False
        results.append(entry)
    return results


# -- v1.3.0: Forecast & Analytics ----------------------------------------------

async def forecast_budget(db, agent_id: int) -> dict | None:
    agent = await get_agent(db, agent_id)
    if not agent:
        return None
    spend = agent["current_spend_usd"]
    budget = agent["monthly_budget_usd"]
    remaining = max(budget - spend, 0)
    daily = await get_daily_spend(db, agent_id, days=14)
    if not daily:
        return {
            "agent_id": agent_id, "agent_name": agent["name"],
            "current_spend_usd": round(spend, 6), "monthly_budget_usd": budget,
            "spend_pct": agent["spend_pct"], "daily_burn_rate": 0,
            "projected_monthly_spend": spend, "days_until_cap": None,
            "projected_cap_date": None, "trend": "stable",
            "recommendation": "No usage data yet. Start recording usage to get forecasts.",
        }
    total_cost = sum(d["cost_usd"] for d in daily)
    active_days = len(daily)
    daily_burn = total_cost / active_days if active_days else 0
    if len(daily) >= 4:
        mid = len(daily) // 2
        recent_avg = sum(d["cost_usd"] for d in daily[:mid]) / mid
        older_avg = sum(d["cost_usd"] for d in daily[mid:]) / (len(daily) - mid)
        if older_avg > 0:
            ratio = recent_avg / older_avg
            trend = "accelerating" if ratio > 1.2 else ("decelerating" if ratio < 0.8 else "stable")
        else:
            trend = "stable"
    else:
        trend = "stable"
    projected_monthly = round(daily_burn * 30, 6)
    if daily_burn > 0 and remaining > 0:
        days_left = int(remaining / daily_burn)
        cap_date = (datetime.utcnow() + timedelta(days=days_left)).strftime("%Y-%m-%d")
    else:
        days_left = None
        cap_date = None
    pct = agent["spend_pct"]
    if pct >= 100:
        rec = "Budget exhausted. Reset budget or increase limit to continue."
    elif days_left is not None and days_left <= 3:
        rec = f"Critical: budget will be capped in {days_left} day(s). Consider increasing budget immediately."
    elif days_left is not None and days_left <= 7:
        rec = f"Warning: {days_left} days until cap at current burn rate. Review usage or adjust budget."
    elif trend == "accelerating":
        rec = "Spend is accelerating. Monitor closely or set a lower alert threshold."
    elif trend == "decelerating":
        rec = "Spend is decelerating. Current budget looks healthy."
    else:
        rec = "Spend is stable. Budget is on track."
    return {
        "agent_id": agent_id, "agent_name": agent["name"],
        "current_spend_usd": round(spend, 6), "monthly_budget_usd": budget,
        "spend_pct": agent["spend_pct"], "daily_burn_rate": round(daily_burn, 6),
        "projected_monthly_spend": projected_monthly,
        "days_until_cap": days_left, "projected_cap_date": cap_date,
        "trend": trend, "recommendation": rec,
    }


async def provider_breakdown(db) -> dict:
    db.row_factory = aiosqlite.Row
    agents = await (await db.execute("SELECT * FROM agents ORDER BY provider")).fetchall()
    if not agents:
        return {"providers": [], "total_providers": 0, "highest_spend_provider": None, "most_efficient_provider": None}
    groups = defaultdict(list)
    for a in agents:
        groups[a["provider"]].append(_agent_row(a))
    provider_stats = []
    for provider, agent_list in sorted(groups.items()):
        total_spend = sum(a["current_spend_usd"] for a in agent_list)
        total_budget = sum(a["monthly_budget_usd"] for a in agent_list)
        models = sorted(set(a["model"] for a in agent_list))
        utilization = round((total_spend / total_budget) * 100, 1) if total_budget > 0 else 0
        provider_stats.append({
            "provider": provider, "agent_count": len(agent_list),
            "total_spend_usd": round(total_spend, 6), "total_budget_usd": round(total_budget, 2),
            "avg_spend_per_agent": round(total_spend / len(agent_list), 6),
            "utilization_pct": utilization, "models": models,
        })
    highest = max(provider_stats, key=lambda p: p["total_spend_usd"])
    most_efficient = min(provider_stats, key=lambda p: p["utilization_pct"])
    return {
        "providers": provider_stats, "total_providers": len(provider_stats),
        "highest_spend_provider": highest["provider"],
        "most_efficient_provider": most_efficient["provider"],
    }


async def export_usage_csv(db, agent_id: int) -> str:
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        "SELECT * FROM usage WHERE agent_id=? ORDER BY recorded_at DESC", (agent_id,)
    )).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "agent_id", "tokens_in", "tokens_out", "cost_usd", "request_id", "metadata", "recorded_at"])
    for r in rows:
        writer.writerow([r["id"], r["agent_id"], r["tokens_in"], r["tokens_out"],
                         r["cost_usd"], r["request_id"] or "", r["metadata"] or "", r["recorded_at"]])
    return output.getvalue()


async def adjust_budget(db, agent_id: int, new_budget: float, reason: str) -> dict | None:
    agent = await get_agent(db, agent_id)
    if not agent:
        return None
    old_budget = agent["monthly_budget_usd"]
    now = datetime.utcnow().isoformat()
    await db.execute("UPDATE agents SET monthly_budget_usd=? WHERE id=?", (new_budget, agent_id))
    await db.execute(
        "INSERT INTO budget_adjustments (agent_id, old_budget_usd, new_budget_usd, reason, adjusted_at) VALUES (?,?,?,?,?)",
        (agent_id, old_budget, new_budget, reason, now),
    )
    alert_type = "budget_increased" if new_budget > old_budget else "budget_decreased"
    updated = await get_agent(db, agent_id)
    await db.execute(
        "INSERT INTO alerts (agent_id, agent_name, alert_type, spend_usd, budget_usd, spend_pct, created_at) VALUES (?,?,?,?,?,?,?)",
        (agent_id, updated["name"], alert_type, updated["current_spend_usd"], new_budget, updated["spend_pct"], now),
    )
    await db.commit()
    # v1.8.0: log activity
    await log_agent_activity(db, agent_id, updated["name"], "budget_adjusted", "budget", {
        "old_budget_usd": old_budget, "new_budget_usd": new_budget, "reason": reason,
    })
    return {
        "agent_id": agent_id, "agent_name": updated["name"],
        "old_budget_usd": old_budget, "new_budget_usd": new_budget,
        "reason": reason, "adjusted_at": now,
        "new_spend_pct": updated["spend_pct"], "new_status": updated["status"],
    }


# -- v1.4.0: Tags, Budget History, Anomalies ----------------------------------

async def get_budget_history(db, agent_id: int) -> list[dict]:
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        "SELECT * FROM budget_adjustments WHERE agent_id=? ORDER BY adjusted_at DESC",
        (agent_id,),
    )).fetchall()
    return [
        {"id": r["id"], "old_budget_usd": r["old_budget_usd"],
         "new_budget_usd": r["new_budget_usd"], "reason": r["reason"],
         "adjusted_at": r["adjusted_at"]}
        for r in rows
    ]


async def get_tag_analytics(db) -> dict:
    db.row_factory = aiosqlite.Row
    agents = await (await db.execute("SELECT * FROM agents")).fetchall()
    tag_map = defaultdict(list)
    for a in agents:
        parsed = _agent_row(a)
        for tag in parsed["tags"]:
            tag_map[tag].append(parsed)
    tags = []
    for tag, agent_list in sorted(tag_map.items()):
        total_spend = sum(a["current_spend_usd"] for a in agent_list)
        total_budget = sum(a["monthly_budget_usd"] for a in agent_list)
        utilization = round((total_spend / total_budget) * 100, 1) if total_budget > 0 else 0
        tags.append({
            "tag": tag, "agent_count": len(agent_list),
            "total_spend_usd": round(total_spend, 6),
            "total_budget_usd": round(total_budget, 2),
            "utilization_pct": utilization,
        })
    return {"tags": tags, "total_tags": len(tags)}


async def get_spend_anomalies(db, agent_id: int, days: int = 30, threshold: float = 2.0) -> dict | None:
    agent = await get_agent(db, agent_id)
    if not agent:
        return None
    daily = await get_daily_spend(db, agent_id, days=days)
    if len(daily) < 3:
        return {
            "agent_id": agent_id, "agent_name": agent["name"],
            "anomalies": [], "total_anomalies": 0,
            "baseline_avg_daily": 0,
        }
    costs = [d["cost_usd"] for d in daily]
    avg_cost = sum(costs) / len(costs) if costs else 0
    anomalies = []
    for d in daily:
        if avg_cost > 0:
            ratio = d["cost_usd"] / avg_cost
            if ratio >= threshold:
                severity = "critical" if ratio >= 3.0 else "warning"
                anomalies.append({
                    "day": d["day"],
                    "cost_usd": round(d["cost_usd"], 6),
                    "avg_cost_usd": round(avg_cost, 6),
                    "deviation_ratio": round(ratio, 2),
                    "severity": severity,
                })
    return {
        "agent_id": agent_id, "agent_name": agent["name"],
        "anomalies": anomalies, "total_anomalies": len(anomalies),
        "baseline_avg_daily": round(avg_cost, 6),
    }


# -- v1.5.0: Rate Limits, Usage Comparison, Alert Ack -------------------------

async def set_rate_limit(db, agent_id: int, requests_per_minute: int, tokens_per_hour: int) -> dict | None:
    agent = await get_agent(db, agent_id)
    if not agent:
        return None
    now = datetime.utcnow().isoformat()
    await db.execute(
        """INSERT INTO rate_limits (agent_id, requests_per_minute, tokens_per_hour, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(agent_id) DO UPDATE SET
             requests_per_minute=excluded.requests_per_minute,
             tokens_per_hour=excluded.tokens_per_hour,
             updated_at=excluded.updated_at""",
        (agent_id, requests_per_minute, tokens_per_hour, now),
    )
    await db.commit()
    # v1.8.0: log activity
    await log_agent_activity(db, agent_id, agent["name"], "rate_limit_set", "rate_limit", {
        "requests_per_minute": requests_per_minute, "tokens_per_hour": tokens_per_hour,
    })
    return await get_rate_limit(db, agent_id)


async def get_rate_limit(db, agent_id: int) -> dict | None:
    agent = await get_agent(db, agent_id)
    if not agent:
        return None
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT * FROM rate_limits WHERE agent_id=?", (agent_id,))).fetchone()
    if not r:
        return {
            "agent_id": agent_id, "agent_name": agent["name"],
            "requests_per_minute": 0, "tokens_per_hour": 0,
            "current_rpm": 0, "current_tph": 0,
            "rpm_utilization_pct": 0.0, "tph_utilization_pct": 0.0,
            "is_throttled": False, "updated_at": "",
        }
    rpm_limit = r["requests_per_minute"]
    tph_limit = r["tokens_per_hour"]

    one_min_ago = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
    rpm_row = await (await db.execute(
        "SELECT COUNT(*) FROM usage WHERE agent_id=? AND recorded_at >= ?",
        (agent_id, one_min_ago),
    )).fetchone()
    current_rpm = rpm_row[0] if rpm_row else 0

    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    tph_row = await (await db.execute(
        "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM usage WHERE agent_id=? AND recorded_at >= ?",
        (agent_id, one_hour_ago),
    )).fetchone()
    current_tph = tph_row[0] if tph_row else 0

    rpm_util = round((current_rpm / rpm_limit) * 100, 1) if rpm_limit > 0 else 0
    tph_util = round((current_tph / tph_limit) * 100, 1) if tph_limit > 0 else 0
    is_throttled = current_rpm >= rpm_limit or current_tph >= tph_limit

    return {
        "agent_id": agent_id, "agent_name": agent["name"],
        "requests_per_minute": rpm_limit, "tokens_per_hour": tph_limit,
        "current_rpm": current_rpm, "current_tph": current_tph,
        "rpm_utilization_pct": rpm_util, "tph_utilization_pct": tph_util,
        "is_throttled": is_throttled, "updated_at": r["updated_at"],
    }


async def check_rate_limit(db, agent_id: int) -> dict | None:
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT * FROM rate_limits WHERE agent_id=?", (agent_id,))).fetchone()
    if not r:
        return None

    one_min_ago = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
    rpm_row = await (await db.execute(
        "SELECT COUNT(*) FROM usage WHERE agent_id=? AND recorded_at >= ?",
        (agent_id, one_min_ago),
    )).fetchone()
    current_rpm = rpm_row[0] if rpm_row else 0

    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    tph_row = await (await db.execute(
        "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM usage WHERE agent_id=? AND recorded_at >= ?",
        (agent_id, one_hour_ago),
    )).fetchone()
    current_tph = tph_row[0] if tph_row else 0

    exceeded_rpm = current_rpm >= r["requests_per_minute"]
    exceeded_tph = current_tph >= r["tokens_per_hour"]

    if exceeded_rpm or exceeded_tph:
        reasons = []
        if exceeded_rpm:
            reasons.append(f"RPM limit {r['requests_per_minute']} reached ({current_rpm} requests/min)")
        if exceeded_tph:
            reasons.append(f"TPH limit {r['tokens_per_hour']} reached ({current_tph} tokens/hour)")
        return {"throttled": True, "reason": "; ".join(reasons)}
    return {"throttled": False, "reason": ""}


async def compare_agents(db, agent_ids: list[int], days: int = 30) -> dict | None:
    entries = []
    for aid in agent_ids:
        agent = await get_agent(db, aid)
        if not agent:
            return None
        daily = await get_daily_spend(db, aid, days=days)
        total_spend = sum(d["cost_usd"] for d in daily)
        total_requests = sum(d["requests"] for d in daily)
        total_tin = sum(d["tokens_in"] for d in daily)
        total_tout = sum(d["tokens_out"] for d in daily)
        avg_cost = round(total_spend / total_requests, 6) if total_requests else 0
        daily_avg = round(total_spend / max(len(daily), 1), 6)
        entries.append({
            "agent_id": aid,
            "agent_name": agent["name"],
            "model": agent["model"],
            "provider": agent["provider"],
            "total_spend_usd": round(total_spend, 6),
            "monthly_budget_usd": agent["monthly_budget_usd"],
            "spend_pct": agent["spend_pct"],
            "request_count": total_requests,
            "avg_cost_per_request": avg_cost,
            "tokens_in_total": total_tin,
            "tokens_out_total": total_tout,
            "daily_avg_spend": daily_avg,
            "status": agent["status"],
        })
    if not entries:
        return None
    cheapest = min(entries, key=lambda e: e["avg_cost_per_request"] if e["request_count"] > 0 else float("inf"))
    most_active = max(entries, key=lambda e: e["request_count"])
    highest_spend = max(entries, key=lambda e: e["total_spend_usd"])
    combined = sum(e["total_spend_usd"] for e in entries)
    return {
        "agents": entries,
        "period_days": days,
        "cheapest_agent_id": cheapest["agent_id"],
        "most_active_agent_id": most_active["agent_id"],
        "highest_spend_agent_id": highest_spend["agent_id"],
        "total_combined_spend": round(combined, 6),
    }


async def acknowledge_alert(db, alert_id: int) -> dict | None:
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT * FROM alerts WHERE id=?", (alert_id,))).fetchone()
    if not r:
        return None
    now = datetime.utcnow().isoformat()
    await db.execute(
        "UPDATE alerts SET acknowledged=1, acknowledged_at=? WHERE id=?",
        (now, alert_id),
    )
    await db.commit()
    return {
        "id": r["id"], "agent_id": r["agent_id"],
        "alert_type": r["alert_type"],
        "acknowledged": True, "acknowledged_at": now,
    }


# -- v1.6.0: Agent Groups, Daily Quotas, Cost Reports -------------------------

async def create_group(db, data: dict) -> dict:
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        "INSERT INTO agent_groups (name, description, budget_usd, created_at) VALUES (?,?,?,?)",
        (data["name"], data.get("description"), data.get("budget_usd"), now),
    )
    await db.commit()
    return await get_group(db, cur.lastrowid)


async def list_groups(db) -> list[dict]:
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute("SELECT * FROM agent_groups ORDER BY id DESC")).fetchall()
    results = []
    for r in rows:
        results.append(await _build_group(db, r))
    return results


async def get_group(db, group_id: int) -> dict | None:
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT * FROM agent_groups WHERE id=?", (group_id,))).fetchone()
    if not r:
        return None
    return await _build_group(db, r)


async def _build_group(db, r) -> dict:
    db.row_factory = aiosqlite.Row
    members = await (await db.execute(
        """SELECT a.* FROM agents a
           JOIN agent_group_members gm ON a.id = gm.agent_id
           WHERE gm.group_id = ?
           ORDER BY a.current_spend_usd DESC""",
        (r["id"],),
    )).fetchall()
    member_list = []
    total_spend = 0.0
    total_budget = 0.0
    for m in members:
        parsed = _agent_row(m)
        member_list.append({
            "agent_id": parsed["id"],
            "agent_name": parsed["name"],
            "model": parsed["model"],
            "current_spend_usd": round(parsed["current_spend_usd"], 6),
            "monthly_budget_usd": parsed["monthly_budget_usd"],
            "status": parsed["status"],
        })
        total_spend += parsed["current_spend_usd"]
        total_budget += parsed["monthly_budget_usd"]

    group_budget = r["budget_usd"]
    effective_budget = group_budget if group_budget else total_budget
    utilization = round((total_spend / effective_budget) * 100, 1) if effective_budget > 0 else 0

    return {
        "id": r["id"],
        "name": r["name"],
        "description": r["description"],
        "budget_usd": group_budget,
        "member_count": len(member_list),
        "total_spend_usd": round(total_spend, 6),
        "total_budget_usd": round(effective_budget, 2),
        "utilization_pct": utilization,
        "members": member_list,
        "created_at": r["created_at"],
    }


async def update_group(db, group_id: int, updates: dict) -> dict | None:
    r = await (await db.execute("SELECT id FROM agent_groups WHERE id=?", (group_id,))).fetchone()
    if not r:
        return None
    fields = {}
    for k in ("name", "description", "budget_usd"):
        if k in updates and updates[k] is not None:
            fields[k] = updates[k]
    if fields:
        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [group_id]
        await db.execute(f"UPDATE agent_groups SET {set_clause} WHERE id=?", values)
        await db.commit()
    return await get_group(db, group_id)


async def delete_group(db, group_id: int) -> bool:
    r = await (await db.execute("SELECT id FROM agent_groups WHERE id=?", (group_id,))).fetchone()
    if not r:
        return False
    await db.execute("DELETE FROM agent_group_members WHERE group_id=?", (group_id,))
    await db.execute("DELETE FROM agent_groups WHERE id=?", (group_id,))
    await db.commit()
    return True


async def add_agent_to_group(db, group_id: int, agent_id: int) -> dict | None:
    grp = await (await db.execute("SELECT id FROM agent_groups WHERE id=?", (group_id,))).fetchone()
    if not grp:
        return None
    agent = await get_agent(db, agent_id)
    if not agent:
        raise ValueError("Agent not found")
    now = datetime.utcnow().isoformat()
    try:
        await db.execute(
            "INSERT INTO agent_group_members (group_id, agent_id, added_at) VALUES (?,?,?)",
            (group_id, agent_id, now),
        )
        await db.commit()
    except Exception as e:
        if "UNIQUE" in str(e) or "PRIMARY" in str(e):
            raise ValueError("Agent already in this group")
        raise
    # v1.8.0: log activity
    await log_agent_activity(db, agent_id, agent["name"], "group_member_added", "group", {
        "group_id": group_id,
    })
    return await get_group(db, group_id)


async def remove_agent_from_group(db, group_id: int, agent_id: int) -> dict | None:
    grp = await (await db.execute("SELECT id FROM agent_groups WHERE id=?", (group_id,))).fetchone()
    if not grp:
        return None
    cur = await db.execute(
        "DELETE FROM agent_group_members WHERE group_id=? AND agent_id=?",
        (group_id, agent_id),
    )
    await db.commit()
    if cur.rowcount == 0:
        raise ValueError("Agent not in this group")
    # v1.8.0: log activity — get agent name (might already be deleted, so handle gracefully)
    agent = await get_agent(db, agent_id)
    agent_name = agent["name"] if agent else f"agent-{agent_id}"
    await log_agent_activity(db, agent_id, agent_name, "group_member_removed", "group", {
        "group_id": group_id,
    })
    return await get_group(db, group_id)


async def set_daily_quota(db, agent_id: int, quota_usd: float) -> dict | None:
    agent = await get_agent(db, agent_id)
    if not agent:
        return None
    await db.execute("UPDATE agents SET daily_quota_usd=? WHERE id=?", (quota_usd, agent_id))
    await db.commit()
    # v1.8.0: log activity
    await log_agent_activity(db, agent_id, agent["name"], "daily_quota_set", "quota", {
        "daily_quota_usd": quota_usd,
    })
    today_spend = await _get_daily_spend_today(db, agent_id)
    pct = round((today_spend / quota_usd) * 100, 1) if quota_usd > 0 else 0
    return {
        "agent_id": agent_id,
        "agent_name": agent["name"],
        "daily_quota_usd": quota_usd,
        "today_spend_usd": round(today_spend, 6),
        "today_pct": pct,
        "remaining_usd": round(max(quota_usd - today_spend, 0), 6),
        "is_over_quota": today_spend >= quota_usd,
    }


async def get_daily_quota(db, agent_id: int) -> dict | None:
    agent = await get_agent(db, agent_id)
    if not agent:
        return None
    quota = agent.get("daily_quota_usd")
    if not quota:
        return {
            "agent_id": agent_id, "agent_name": agent["name"],
            "daily_quota_usd": 0, "today_spend_usd": 0,
            "today_pct": 0, "remaining_usd": 0,
            "is_over_quota": False,
        }
    today_spend = await _get_daily_spend_today(db, agent_id)
    pct = round((today_spend / quota) * 100, 1) if quota > 0 else 0
    return {
        "agent_id": agent_id,
        "agent_name": agent["name"],
        "daily_quota_usd": quota,
        "today_spend_usd": round(today_spend, 6),
        "today_pct": pct,
        "remaining_usd": round(max(quota - today_spend, 0), 6),
        "is_over_quota": today_spend >= quota,
    }


async def get_cost_report(db, days: int = 30) -> dict:
    """Generate cost allocation report grouped by tag, provider, and model."""
    db.row_factory = aiosqlite.Row
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    agents = await (await db.execute("SELECT * FROM agents")).fetchall()

    # Build request count per agent in period
    agent_requests = {}
    for a in agents:
        req_row = await (await db.execute(
            "SELECT COUNT(*) FROM usage WHERE agent_id=? AND date(recorded_at)>=?",
            (a["id"], cutoff),
        )).fetchone()
        agent_requests[a["id"]] = req_row[0] if req_row else 0

    parsed_agents = [_agent_row(a) for a in agents]

    # By tag
    tag_map = defaultdict(lambda: {"agents": [], "requests": 0})
    for i, a in enumerate(parsed_agents):
        for tag in a["tags"]:
            tag_map[tag]["agents"].append(a)
            tag_map[tag]["requests"] += agent_requests[a["id"]]
    by_tag = []
    for tag, data in sorted(tag_map.items()):
        ts = sum(a["current_spend_usd"] for a in data["agents"])
        tb = sum(a["monthly_budget_usd"] for a in data["agents"])
        by_tag.append({
            "dimension": "tag", "value": tag,
            "agent_count": len(data["agents"]),
            "total_spend_usd": round(ts, 6),
            "total_budget_usd": round(tb, 2),
            "utilization_pct": round((ts / tb) * 100, 1) if tb > 0 else 0,
            "request_count": data["requests"],
        })

    # By provider
    prov_map = defaultdict(lambda: {"agents": [], "requests": 0})
    for a in parsed_agents:
        prov_map[a["provider"]]["agents"].append(a)
        prov_map[a["provider"]]["requests"] += agent_requests[a["id"]]
    by_provider = []
    for prov, data in sorted(prov_map.items()):
        ts = sum(a["current_spend_usd"] for a in data["agents"])
        tb = sum(a["monthly_budget_usd"] for a in data["agents"])
        by_provider.append({
            "dimension": "provider", "value": prov,
            "agent_count": len(data["agents"]),
            "total_spend_usd": round(ts, 6),
            "total_budget_usd": round(tb, 2),
            "utilization_pct": round((ts / tb) * 100, 1) if tb > 0 else 0,
            "request_count": data["requests"],
        })

    # By model
    model_map = defaultdict(lambda: {"agents": [], "requests": 0})
    for a in parsed_agents:
        model_map[a["model"]]["agents"].append(a)
        model_map[a["model"]]["requests"] += agent_requests[a["id"]]
    by_model = []
    for model, data in sorted(model_map.items()):
        ts = sum(a["current_spend_usd"] for a in data["agents"])
        tb = sum(a["monthly_budget_usd"] for a in data["agents"])
        by_model.append({
            "dimension": "model", "value": model,
            "agent_count": len(data["agents"]),
            "total_spend_usd": round(ts, 6),
            "total_budget_usd": round(tb, 2),
            "utilization_pct": round((ts / tb) * 100, 1) if tb > 0 else 0,
            "request_count": data["requests"],
        })

    total_spend = sum(a["current_spend_usd"] for a in parsed_agents)
    total_budget = sum(a["monthly_budget_usd"] for a in parsed_agents)

    return {
        "period_days": days,
        "by_tag": by_tag,
        "by_provider": by_provider,
        "by_model": by_model,
        "total_spend_usd": round(total_spend, 6),
        "total_budget_usd": round(total_budget, 2),
        "overall_utilization_pct": round((total_spend / total_budget) * 100, 1) if total_budget > 0 else 0,
    }


# -- v1.7.0: Agent Cloning, Hourly Usage, Batch Status ------------------------

async def clone_agent(db, agent_id: int, new_name: str | None = None,
                      include_rate_limit: bool = True,
                      include_daily_quota: bool = True,
                      include_groups: bool = True) -> dict | None:
    """Clone an agent with all its settings but fresh spend counters."""
    db.row_factory = aiosqlite.Row
    original = await (await db.execute("SELECT * FROM agents WHERE id=?", (agent_id,))).fetchone()
    if not original:
        return None

    clone_name = new_name or f"{original['name']}-clone"

    # Check name uniqueness
    existing = await (await db.execute("SELECT id FROM agents WHERE name=?", (clone_name,))).fetchone()
    if existing:
        raise ValueError(f"Agent name '{clone_name}' already exists")

    now = datetime.utcnow().isoformat()
    tags = original["tags"] if "tags" in original.keys() else "[]"
    daily_quota = None
    if include_daily_quota:
        try:
            daily_quota = original["daily_quota_usd"]
        except (IndexError, KeyError):
            pass

    cur = await db.execute(
        """INSERT INTO agents (name, provider, model, monthly_budget_usd, alert_threshold_pct,
           webhook_url, tags, daily_quota_usd, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (clone_name, original["provider"], original["model"],
         original["monthly_budget_usd"], original["alert_threshold_pct"],
         original["webhook_url"], tags, daily_quota, now),
    )
    clone_id = cur.lastrowid
    await db.commit()

    # Copy rate limit if exists
    if include_rate_limit:
        rl = await (await db.execute("SELECT * FROM rate_limits WHERE agent_id=?", (agent_id,))).fetchone()
        if rl:
            await db.execute(
                """INSERT INTO rate_limits (agent_id, requests_per_minute, tokens_per_hour, updated_at)
                   VALUES (?,?,?,?)""",
                (clone_id, rl["requests_per_minute"], rl["tokens_per_hour"], now),
            )
            await db.commit()

    # Add to same groups
    if include_groups:
        groups = await (await db.execute(
            "SELECT group_id FROM agent_group_members WHERE agent_id=?", (agent_id,)
        )).fetchall()
        for g in groups:
            try:
                await db.execute(
                    "INSERT INTO agent_group_members (group_id, agent_id, added_at) VALUES (?,?,?)",
                    (g["group_id"], clone_id, now),
                )
            except Exception:
                pass
        await db.commit()

    clone = await get_agent(db, clone_id)
    # v1.8.0: log activity
    await log_agent_activity(db, clone_id, clone_name, "agent_cloned", "config", {
        "cloned_from_id": agent_id, "cloned_from_name": original["name"],
        "include_rate_limit": include_rate_limit, "include_daily_quota": include_daily_quota,
        "include_groups": include_groups,
    })
    return {
        "cloned_from": agent_id,
        "cloned_from_name": original["name"],
        "agent": clone,
    }


async def get_hourly_usage(db, agent_id: int, days: int = 30) -> dict | None:
    """Aggregate usage by hour of day for pattern analysis."""
    agent = await get_agent(db, agent_id)
    if not agent:
        return None

    db.row_factory = aiosqlite.Row
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = await (await db.execute(
        """SELECT CAST(strftime('%%H', recorded_at) AS INTEGER) as hour,
                  COUNT(*) as requests,
                  COALESCE(SUM(tokens_in), 0) as tin,
                  COALESCE(SUM(tokens_out), 0) as tout,
                  COALESCE(SUM(cost_usd), 0) as cost
           FROM usage
           WHERE agent_id=? AND date(recorded_at) >= ?
           GROUP BY hour
           ORDER BY hour""",
        (agent_id, cutoff),
    )).fetchall()

    # Build full 24-hour breakdown (fill missing hours with zeros)
    hour_data = {r["hour"]: r for r in rows}
    hours = []
    for h in range(24):
        if h in hour_data:
            r = hour_data[h]
            reqs = r["requests"]
            cost = round(r["cost"] or 0, 6)
            hours.append({
                "hour": h,
                "requests": reqs,
                "tokens_in": r["tin"] or 0,
                "tokens_out": r["tout"] or 0,
                "cost_usd": cost,
                "avg_cost_per_request": round(cost / reqs, 6) if reqs > 0 else 0,
            })
        else:
            hours.append({
                "hour": h, "requests": 0, "tokens_in": 0,
                "tokens_out": 0, "cost_usd": 0, "avg_cost_per_request": 0,
            })

    total_requests = sum(h["requests"] for h in hours)
    active_hours = [h for h in hours if h["requests"] > 0]
    peak_hour = max(hours, key=lambda h: h["requests"])["hour"] if active_hours else 0
    quietest_hour = min(active_hours, key=lambda h: h["requests"])["hour"] if active_hours else 0

    return {
        "agent_id": agent_id,
        "agent_name": agent["name"],
        "period_days": days,
        "hours": hours,
        "peak_hour": peak_hour,
        "quietest_hour": quietest_hour,
        "total_requests": total_requests,
    }


async def batch_agent_status(db, agent_ids: list[int]) -> dict:
    """Get status of multiple agents in a single call."""
    agents = []
    not_found = 0
    for aid in agent_ids:
        agent = await get_agent(db, aid)
        if agent:
            agents.append(agent)
        else:
            not_found += 1

    ok = sum(1 for a in agents if a["status"] == "ok")
    warning = sum(1 for a in agents if a["status"] == "warning")
    capped = sum(1 for a in agents if a["status"] == "capped")
    total_spend = sum(a["current_spend_usd"] for a in agents)
    total_budget = sum(a["monthly_budget_usd"] for a in agents)

    return {
        "agents": agents,
        "summary": {
            "total": len(agents),
            "ok": ok,
            "warning": warning,
            "capped": capped,
            "not_found": not_found,
            "total_spend_usd": round(total_spend, 6),
            "total_budget_usd": round(total_budget, 2),
        },
    }


# -- v1.8.0: Spend Snapshots --------------------------------------------------

async def create_snapshot(db, snapshot_type: str = "manual") -> dict:
    """Gather all current metrics into a spend snapshot row."""
    valid_types = {"daily", "weekly", "monthly", "manual"}
    if snapshot_type not in valid_types:
        raise ValueError(f"Invalid snapshot_type: {snapshot_type}. Must be one of: {', '.join(sorted(valid_types))}")

    db.row_factory = aiosqlite.Row
    now = datetime.utcnow().isoformat()

    # Gather metrics
    agents = await (await db.execute("SELECT * FROM agents")).fetchall()
    total_agents = len(agents)
    parsed = [_agent_row(a) for a in agents]
    total_budget = sum(a["monthly_budget_usd"] for a in parsed)
    total_spend = sum(a["current_spend_usd"] for a in parsed)
    utilization = round((total_spend / total_budget) * 100, 1) if total_budget > 0 else 0.0
    avg_agent_spend = round(total_spend / total_agents, 6) if total_agents > 0 else 0.0

    # Active agents (those with any usage today)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    active_row = await (await db.execute(
        "SELECT COUNT(DISTINCT agent_id) FROM usage WHERE date(recorded_at) = ?", (today,)
    )).fetchone()
    active_agents = active_row[0] if active_row else 0

    # Alerts
    total_alerts_row = await (await db.execute("SELECT COUNT(*) FROM alerts")).fetchone()
    total_alerts = total_alerts_row[0] if total_alerts_row else 0
    unack_row = await (await db.execute("SELECT COUNT(*) FROM alerts WHERE acknowledged = 0")).fetchone()
    unacknowledged_alerts = unack_row[0] if unack_row else 0

    # Top spender
    top_spender_id = None
    top_spender_name = None
    top_spender_spend = 0.0
    if parsed:
        top = max(parsed, key=lambda a: a["current_spend_usd"])
        top_spender_id = str(top["id"])
        top_spender_name = top["name"]
        top_spender_spend = round(top["current_spend_usd"], 6)

    # Groups count
    grp_row = await (await db.execute("SELECT COUNT(*) FROM agent_groups")).fetchone()
    groups_count = grp_row[0] if grp_row else 0

    cur = await db.execute(
        """INSERT INTO spend_snapshots
           (snapshot_type, total_agents, active_agents, total_budget_usd, total_spend_usd,
            utilization_pct, total_alerts, unacknowledged_alerts,
            top_spender_id, top_spender_name, top_spender_spend,
            groups_count, avg_agent_spend, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (snapshot_type, total_agents, active_agents, round(total_budget, 2), round(total_spend, 6),
         utilization, total_alerts, unacknowledged_alerts,
         top_spender_id, top_spender_name, top_spender_spend,
         groups_count, avg_agent_spend, now),
    )
    await db.commit()
    return await get_snapshot(db, cur.lastrowid)


async def list_snapshots(db, snapshot_type: str | None = None, limit: int = 50) -> list[dict]:
    """List snapshots with optional filters."""
    db.row_factory = aiosqlite.Row
    conditions = []
    params = []
    if snapshot_type is not None:
        conditions.append("snapshot_type = ?")
        params.append(snapshot_type)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    rows = await (await db.execute(
        f"SELECT * FROM spend_snapshots {where} ORDER BY id DESC LIMIT ?", params,
    )).fetchall()
    return [_snapshot_row(r) for r in rows]


async def get_snapshot(db, snapshot_id: int) -> dict | None:
    """Get a single snapshot by ID."""
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT * FROM spend_snapshots WHERE id=?", (snapshot_id,))).fetchone()
    if not r:
        return None
    return _snapshot_row(r)


async def delete_snapshot(db, snapshot_id: int) -> bool:
    """Delete a snapshot."""
    r = await (await db.execute("SELECT id FROM spend_snapshots WHERE id=?", (snapshot_id,))).fetchone()
    if not r:
        return False
    await db.execute("DELETE FROM spend_snapshots WHERE id=?", (snapshot_id,))
    await db.commit()
    return True


async def get_snapshot_trend(db, days: int = 30, snapshot_type: str | None = None) -> dict:
    """Get snapshot trend over a time period."""
    db.row_factory = aiosqlite.Row
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conditions = ["created_at >= ?"]
    params = [cutoff]
    if snapshot_type is not None:
        conditions.append("snapshot_type = ?")
        params.append(snapshot_type)
    where = f"WHERE {' AND '.join(conditions)}"
    rows = await (await db.execute(
        f"SELECT * FROM spend_snapshots {where} ORDER BY created_at ASC", params,
    )).fetchall()
    snapshots = [_snapshot_row(r) for r in rows]

    # Compute trend
    if len(snapshots) < 2:
        trend = "stable"
    else:
        mid = len(snapshots) // 2
        first_half_avg = sum(s["total_spend_usd"] for s in snapshots[:mid]) / mid
        second_half_avg = sum(s["total_spend_usd"] for s in snapshots[mid:]) / (len(snapshots) - mid)
        if first_half_avg > 0:
            ratio = second_half_avg / first_half_avg
            if ratio > 1.1:
                trend = "increasing"
            elif ratio < 0.9:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable" if second_half_avg == 0 else "increasing"

    # Average utilization
    avg_util = 0.0
    if snapshots:
        avg_util = round(sum(s["utilization_pct"] for s in snapshots) / len(snapshots), 1)

    return {
        "snapshots": snapshots,
        "spend_trend": trend,
        "avg_utilization": avg_util,
    }


def _snapshot_row(r) -> dict:
    """Convert a spend_snapshots row to a response dict."""
    return {
        "id": r["id"],
        "snapshot_type": r["snapshot_type"],
        "total_agents": r["total_agents"],
        "active_agents": r["active_agents"],
        "total_budget_usd": r["total_budget_usd"],
        "total_spend_usd": r["total_spend_usd"],
        "utilization_pct": r["utilization_pct"],
        "total_alerts": r["total_alerts"],
        "unacknowledged_alerts": r["unacknowledged_alerts"],
        "top_spender_id": r["top_spender_id"],
        "top_spender_name": r["top_spender_name"],
        "top_spender_spend": r["top_spender_spend"],
        "groups_count": r["groups_count"],
        "avg_agent_spend": r["avg_agent_spend"],
        "created_at": r["created_at"],
    }


# -- v1.9.0: Cost Optimizations -----------------------------------------------

async def get_agent_optimizations(db, agent_id: int, days: int = 30) -> dict | None:
    """Analyze an agent's usage patterns and suggest cost optimizations."""
    agent = await get_agent(db, agent_id)
    if not agent:
        return None

    db.row_factory = aiosqlite.Row
    suggestions = []
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # --- 1. Underutilized agent detection ---
    req_row = await (await db.execute(
        "SELECT COUNT(*) FROM usage WHERE agent_id=? AND date(recorded_at) >= ?",
        (agent_id, cutoff),
    )).fetchone()
    request_count = req_row[0] if req_row else 0

    if request_count < 10 and agent["monthly_budget_usd"] > 10:
        suggestions.append({
            "type": "underutilized",
            "severity": "medium",
            "agent_id": agent_id,
            "agent_name": agent["name"],
            "estimated_savings_usd": round(agent["monthly_budget_usd"] * 0.5, 2),
            "description": f"Agent '{agent['name']}' has only {request_count} requests in {days} days with a ${agent['monthly_budget_usd']} budget. Consider reducing budget or consolidating with another agent.",
            "details": {
                "request_count": request_count,
                "period_days": days,
                "monthly_budget_usd": agent["monthly_budget_usd"],
            },
        })

    # --- 2. Budget right-sizing (spend consistently <50% of budget) ---
    spend_pct = agent["spend_pct"]
    if spend_pct < 50 and agent["current_spend_usd"] > 0:
        right_sized_budget = round(agent["current_spend_usd"] * 2, 2)  # 2x actual spend as buffer
        savings = round(agent["monthly_budget_usd"] - right_sized_budget, 2)
        if savings > 0:
            suggestions.append({
                "type": "budget_right_size",
                "severity": "low" if spend_pct > 30 else "medium",
                "agent_id": agent_id,
                "agent_name": agent["name"],
                "estimated_savings_usd": savings,
                "description": f"Agent '{agent['name']}' is using only {spend_pct}% of its ${agent['monthly_budget_usd']} budget. Consider right-sizing to ~${right_sized_budget}.",
                "details": {
                    "current_spend_usd": agent["current_spend_usd"],
                    "monthly_budget_usd": agent["monthly_budget_usd"],
                    "spend_pct": spend_pct,
                    "suggested_budget_usd": right_sized_budget,
                },
            })

    # --- 3. Cost spike detection (recent per-request cost vs historical average) ---
    daily = await get_daily_spend(db, agent_id, days=days)
    if len(daily) >= 7:
        costs_with_requests = [(d["cost_usd"], d["requests"]) for d in daily if d["requests"] > 0]
        if len(costs_with_requests) >= 4:
            per_request_costs = [c / r for c, r in costs_with_requests]
            avg_per_request = sum(per_request_costs) / len(per_request_costs)
            # Check last 3 days for spikes
            recent_3 = per_request_costs[:3]
            if avg_per_request > 0:
                for i, recent_cost in enumerate(recent_3):
                    ratio = recent_cost / avg_per_request
                    if ratio > 2.0:
                        spike_day = daily[i]["day"] if i < len(daily) else "recent"
                        excess_cost = round((recent_cost - avg_per_request) * costs_with_requests[i][1], 4)
                        suggestions.append({
                            "type": "cost_spike",
                            "severity": "high" if ratio > 3.0 else "medium",
                            "agent_id": agent_id,
                            "agent_name": agent["name"],
                            "estimated_savings_usd": round(excess_cost, 2),
                            "description": f"Cost spike detected on {spike_day}: ${round(recent_cost, 4)}/request vs avg ${round(avg_per_request, 4)}/request ({round(ratio, 1)}x). Investigate prompt changes or model pricing updates.",
                            "details": {
                                "spike_day": spike_day,
                                "per_request_cost": round(recent_cost, 6),
                                "avg_per_request_cost": round(avg_per_request, 6),
                                "spike_ratio": round(ratio, 2),
                            },
                        })
                        break  # Only report the most recent spike

    # --- 4. Cheaper model alternatives ---
    # Find other agents on the same provider using cheaper models
    provider = agent["provider"]
    model = agent["model"]
    other_agents = await (await db.execute(
        "SELECT * FROM agents WHERE provider=? AND model != ? AND id != ?",
        (provider, model, agent_id),
    )).fetchall()

    if other_agents and request_count > 0:
        # Calculate this agent's avg cost per request
        cost_row = await (await db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM usage WHERE agent_id=? AND date(recorded_at) >= ?",
            (agent_id, cutoff),
        )).fetchone()
        this_total_cost = cost_row[0] if cost_row else 0
        this_count = cost_row[1] if cost_row else 0
        this_avg_cost = this_total_cost / this_count if this_count > 0 else 0

        for other in other_agents:
            other_cost_row = await (await db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM usage WHERE agent_id=? AND date(recorded_at) >= ?",
                (other["id"], cutoff),
            )).fetchone()
            other_total = other_cost_row[0] if other_cost_row else 0
            other_count = other_cost_row[1] if other_cost_row else 0
            if other_count < 5:
                continue  # Not enough data to compare
            other_avg = other_total / other_count
            if this_avg_cost > 0 and other_avg < this_avg_cost * 0.7:
                # This alternative model is at least 30% cheaper
                projected_savings = round((this_avg_cost - other_avg) * this_count, 2)
                if projected_savings > 0:
                    suggestions.append({
                        "type": "cheaper_model",
                        "severity": "medium" if projected_savings < 10 else "high",
                        "agent_id": agent_id,
                        "agent_name": agent["name"],
                        "estimated_savings_usd": projected_savings,
                        "description": f"Model '{other['model']}' on {provider} averages ${round(other_avg, 4)}/request vs your '{model}' at ${round(this_avg_cost, 4)}/request. Potential savings: ${projected_savings} over {days} days.",
                        "details": {
                            "current_model": model,
                            "alternative_model": other["model"],
                            "current_avg_cost": round(this_avg_cost, 6),
                            "alternative_avg_cost": round(other_avg, 6),
                            "request_count": this_count,
                        },
                    })
                    break  # Only suggest the best alternative

    total_savings = round(sum(s["estimated_savings_usd"] for s in suggestions), 2)

    return {
        "agent_id": agent_id,
        "agent_name": agent["name"],
        "suggestions": suggestions,
        "total_suggestions": len(suggestions),
        "total_potential_savings_usd": total_savings,
    }


async def get_optimization_summary(db, days: int = 30) -> dict:
    """Aggregate optimization suggestions across all agents."""
    db.row_factory = aiosqlite.Row
    agents = await (await db.execute("SELECT * FROM agents")).fetchall()

    all_suggestions = []
    agent_savings = []

    for a in agents:
        parsed = _agent_row(a)
        result = await get_agent_optimizations(db, parsed["id"], days=days)
        if result and result["suggestions"]:
            all_suggestions.extend(result["suggestions"])
            agent_savings.append({
                "agent_id": parsed["id"],
                "agent_name": parsed["name"],
                "suggestion_count": result["total_suggestions"],
                "total_savings_usd": result["total_potential_savings_usd"],
            })

    total_potential_savings = round(sum(s["estimated_savings_usd"] for s in all_suggestions), 2)

    # Group by type
    type_map = defaultdict(lambda: {"count": 0, "total_savings_usd": 0.0})
    for s in all_suggestions:
        type_map[s["type"]]["count"] += 1
        type_map[s["type"]]["total_savings_usd"] += s["estimated_savings_usd"]

    by_type = [
        {
            "type": t,
            "count": data["count"],
            "total_savings_usd": round(data["total_savings_usd"], 2),
        }
        for t, data in sorted(type_map.items(), key=lambda x: -x[1]["total_savings_usd"])
    ]

    # Top agents by savings potential
    top_agents = sorted(agent_savings, key=lambda x: -x["total_savings_usd"])[:10]

    return {
        "total_suggestions": len(all_suggestions),
        "total_potential_savings_usd": total_potential_savings,
        "by_type": by_type,
        "top_agents": top_agents,
    }


# -- v1.9.0: Cost Centers / Chargebacks ---------------------------------------

async def create_cost_center(db, data: dict) -> dict:
    """Create a new cost center."""
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        "INSERT INTO cost_centers (name, owner, description, created_at) VALUES (?,?,?,?)",
        (data["name"], data["owner"], data.get("description"), now),
    )
    await db.commit()
    return await get_cost_center(db, cur.lastrowid)


async def list_cost_centers(db) -> list[dict]:
    """List all cost centers."""
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute("SELECT * FROM cost_centers ORDER BY id DESC")).fetchall()
    results = []
    for r in rows:
        results.append(await _build_cost_center(db, r))
    return results


async def get_cost_center(db, center_id: int) -> dict | None:
    """Get a single cost center by ID."""
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT * FROM cost_centers WHERE id=?", (center_id,))).fetchone()
    if not r:
        return None
    return await _build_cost_center(db, r)


async def _build_cost_center(db, r) -> dict:
    """Build a cost center response with agents and spend breakdown."""
    db.row_factory = aiosqlite.Row
    allocations = await (await db.execute(
        """SELECT cca.agent_id, cca.allocation_pct, a.name, a.current_spend_usd
           FROM cost_center_agents cca
           JOIN agents a ON a.id = cca.agent_id
           WHERE cca.cost_center_id = ?
           ORDER BY a.current_spend_usd DESC""",
        (r["id"],),
    )).fetchall()

    agents = []
    total_allocated = 0.0
    for alloc in allocations:
        agent_spend = alloc["current_spend_usd"]
        allocated = round(agent_spend * (alloc["allocation_pct"] / 100.0), 6)
        total_allocated += allocated
        agents.append({
            "agent_id": alloc["agent_id"],
            "agent_name": alloc["name"],
            "allocation_pct": alloc["allocation_pct"],
            "current_spend_usd": round(agent_spend, 6),
            "allocated_spend_usd": allocated,
        })

    return {
        "id": r["id"],
        "name": r["name"],
        "owner": r["owner"],
        "description": r["description"],
        "agents": agents,
        "total_allocated_spend_usd": round(total_allocated, 6),
        "agent_count": len(agents),
        "created_at": r["created_at"],
    }


async def add_agent_to_cost_center(db, center_id: int, agent_id: int, allocation_pct: float) -> dict | None:
    """Assign an agent to a cost center with allocation percentage."""
    cc = await (await db.execute("SELECT id FROM cost_centers WHERE id=?", (center_id,))).fetchone()
    if not cc:
        return None
    agent = await get_agent(db, agent_id)
    if not agent:
        raise ValueError("Agent not found")

    # Check total allocation for this agent across all cost centers doesn't exceed 100%
    existing_row = await (await db.execute(
        "SELECT COALESCE(SUM(allocation_pct), 0) FROM cost_center_agents WHERE agent_id=? AND cost_center_id != ?",
        (agent_id, center_id),
    )).fetchone()
    existing_total = existing_row[0] if existing_row else 0
    if existing_total + allocation_pct > 100:
        raise ValueError(f"Total allocation for agent would be {existing_total + allocation_pct}%, exceeds 100%")

    now = datetime.utcnow().isoformat()
    try:
        await db.execute(
            """INSERT INTO cost_center_agents (cost_center_id, agent_id, allocation_pct, added_at)
               VALUES (?,?,?,?)
               ON CONFLICT(cost_center_id, agent_id) DO UPDATE SET
                 allocation_pct=excluded.allocation_pct,
                 added_at=excluded.added_at""",
            (center_id, agent_id, allocation_pct, now),
        )
        await db.commit()
    except Exception as e:
        raise

    # Log activity
    await log_agent_activity(db, agent_id, agent["name"], "cost_center_assigned", "cost_center", {
        "cost_center_id": center_id, "allocation_pct": allocation_pct,
    })
    return await get_cost_center(db, center_id)


async def remove_agent_from_cost_center(db, center_id: int, agent_id: int) -> dict | None:
    """Remove an agent from a cost center."""
    cc = await (await db.execute("SELECT id FROM cost_centers WHERE id=?", (center_id,))).fetchone()
    if not cc:
        return None
    cur = await db.execute(
        "DELETE FROM cost_center_agents WHERE cost_center_id=? AND agent_id=?",
        (center_id, agent_id),
    )
    await db.commit()
    if cur.rowcount == 0:
        raise ValueError("Agent not assigned to this cost center")

    # Log activity
    agent = await get_agent(db, agent_id)
    agent_name = agent["name"] if agent else f"agent-{agent_id}"
    await log_agent_activity(db, agent_id, agent_name, "cost_center_removed", "cost_center", {
        "cost_center_id": center_id,
    })
    return await get_cost_center(db, center_id)


async def get_chargeback_report(db, center_id: int, days: int = 30) -> dict | None:
    """Generate a chargeback report for a cost center."""
    db.row_factory = aiosqlite.Row
    cc = await (await db.execute("SELECT * FROM cost_centers WHERE id=?", (center_id,))).fetchone()
    if not cc:
        return None

    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    now = datetime.utcnow().isoformat()

    allocations = await (await db.execute(
        """SELECT cca.agent_id, cca.allocation_pct, a.name
           FROM cost_center_agents cca
           JOIN agents a ON a.id = cca.agent_id
           WHERE cca.cost_center_id = ?""",
        (center_id,),
    )).fetchall()

    agents = []
    total_center_spend = 0.0

    for alloc in allocations:
        # Get spend in the period
        spend_row = await (await db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE agent_id=? AND date(recorded_at) >= ?",
            (alloc["agent_id"], cutoff),
        )).fetchone()
        agent_spend = spend_row[0] if spend_row else 0.0
        allocated_cost = round(agent_spend * (alloc["allocation_pct"] / 100.0), 6)
        total_center_spend += allocated_cost

        agents.append({
            "agent_id": alloc["agent_id"],
            "agent_name": alloc["name"],
            "allocation_pct": alloc["allocation_pct"],
            "total_spend_usd": round(agent_spend, 6),
            "allocated_cost_usd": allocated_cost,
        })

    return {
        "cost_center_id": center_id,
        "cost_center_name": cc["name"],
        "owner": cc["owner"],
        "period_days": days,
        "agents": agents,
        "total_cost_center_spend_usd": round(total_center_spend, 6),
        "generated_at": now,
    }


# -- v1.9.0: Notification Channels --------------------------------------------

VALID_CHANNEL_TYPES = {"email", "slack", "webhook"}


async def create_notification_channel(db, data: dict) -> dict:
    """Create a new notification channel."""
    channel_type = data.get("channel_type", "")
    if channel_type not in VALID_CHANNEL_TYPES:
        raise ValueError(f"Invalid channel_type: {channel_type}. Must be one of: {', '.join(sorted(VALID_CHANNEL_TYPES))}")
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        """INSERT INTO notification_channels (name, channel_type, config, created_at, updated_at)
           VALUES (?,?,?,?,?)""",
        (data["name"], channel_type, json.dumps(data.get("config", {})), now, now),
    )
    await db.commit()
    return await get_notification_channel(db, cur.lastrowid)


async def list_notification_channels(db) -> list[dict]:
    """List all notification channels."""
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        "SELECT * FROM notification_channels ORDER BY id DESC"
    )).fetchall()
    return [_channel_row(r) for r in rows]


async def get_notification_channel(db, channel_id: int) -> dict | None:
    """Get a single notification channel by ID."""
    db.row_factory = aiosqlite.Row
    r = await (await db.execute(
        "SELECT * FROM notification_channels WHERE id=?", (channel_id,)
    )).fetchone()
    if not r:
        return None
    return _channel_row(r)


async def update_notification_channel(db, channel_id: int, updates: dict) -> dict | None:
    """Update a notification channel."""
    r = await (await db.execute(
        "SELECT id FROM notification_channels WHERE id=?", (channel_id,)
    )).fetchone()
    if not r:
        return None
    allowed = {"name", "channel_type", "config"}
    fields = {}
    for k, v in updates.items():
        if k in allowed and v is not None:
            if k == "channel_type" and v not in VALID_CHANNEL_TYPES:
                raise ValueError(f"Invalid channel_type: {v}. Must be one of: {', '.join(sorted(VALID_CHANNEL_TYPES))}")
            if k == "config":
                fields[k] = json.dumps(v)
            else:
                fields[k] = v
    if not fields:
        return await get_notification_channel(db, channel_id)
    fields["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [channel_id]
    await db.execute(f"UPDATE notification_channels SET {set_clause} WHERE id=?", values)
    await db.commit()
    return await get_notification_channel(db, channel_id)


async def delete_notification_channel(db, channel_id: int) -> bool:
    """Delete a notification channel and its subscriptions."""
    r = await (await db.execute(
        "SELECT id FROM notification_channels WHERE id=?", (channel_id,)
    )).fetchone()
    if not r:
        return False
    await db.execute("DELETE FROM agent_notification_subscriptions WHERE channel_id=?", (channel_id,))
    await db.execute("DELETE FROM notification_channels WHERE id=?", (channel_id,))
    await db.commit()
    return True


async def subscribe_agent_to_channel(db, agent_id: int, channel_id: int) -> dict | None:
    """Subscribe an agent to a notification channel."""
    agent = await get_agent(db, agent_id)
    if not agent:
        return None
    channel = await get_notification_channel(db, channel_id)
    if not channel:
        raise ValueError("Notification channel not found")
    now = datetime.utcnow().isoformat()
    try:
        await db.execute(
            "INSERT INTO agent_notification_subscriptions (agent_id, channel_id, subscribed_at) VALUES (?,?,?)",
            (agent_id, channel_id, now),
        )
        await db.commit()
    except Exception as e:
        if "UNIQUE" in str(e) or "PRIMARY" in str(e):
            raise ValueError("Agent already subscribed to this channel")
        raise
    # Log activity
    await log_agent_activity(db, agent_id, agent["name"], "channel_subscribed", "notification", {
        "channel_id": channel_id, "channel_name": channel["name"],
    })
    return {"agent_id": agent_id, "channel_id": channel_id, "subscribed_at": now}


async def unsubscribe_agent_from_channel(db, agent_id: int, channel_id: int) -> bool:
    """Unsubscribe an agent from a notification channel."""
    cur = await db.execute(
        "DELETE FROM agent_notification_subscriptions WHERE agent_id=? AND channel_id=?",
        (agent_id, channel_id),
    )
    await db.commit()
    if cur.rowcount == 0:
        return False
    # Log activity
    agent = await get_agent(db, agent_id)
    agent_name = agent["name"] if agent else f"agent-{agent_id}"
    await log_agent_activity(db, agent_id, agent_name, "channel_unsubscribed", "notification", {
        "channel_id": channel_id,
    })
    return True


async def list_agent_channels(db, agent_id: int) -> list[dict] | None:
    """List all channels an agent is subscribed to."""
    agent = await get_agent(db, agent_id)
    if not agent:
        return None
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        """SELECT nc.id, nc.name, nc.channel_type, ans.subscribed_at
           FROM notification_channels nc
           JOIN agent_notification_subscriptions ans ON nc.id = ans.channel_id
           WHERE ans.agent_id = ?
           ORDER BY ans.subscribed_at DESC""",
        (agent_id,),
    )).fetchall()
    return [
        {
            "channel_id": r["id"],
            "channel_name": r["name"],
            "channel_type": r["channel_type"],
            "subscribed_at": r["subscribed_at"],
        }
        for r in rows
    ]


async def test_notification_channel(db, channel_id: int) -> dict | None:
    """Send a test notification through a channel (simulated)."""
    channel = await get_notification_channel(db, channel_id)
    if not channel:
        return None

    channel_type = channel["channel_type"]
    config = channel["config"]

    # Simulate sending based on channel type
    if channel_type == "webhook":
        url = config.get("url", "")
        if url:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(url, json={
                        "event": "test",
                        "channel_name": channel["name"],
                        "message": "This is a test notification from AgentCap.",
                    })
                return {
                    "channel_id": channel_id,
                    "channel_name": channel["name"],
                    "channel_type": channel_type,
                    "status": "sent",
                    "message": f"Test webhook sent to {url}, response status: {resp.status_code}",
                }
            except Exception as e:
                return {
                    "channel_id": channel_id,
                    "channel_name": channel["name"],
                    "channel_type": channel_type,
                    "status": "error",
                    "message": f"Failed to send test webhook: {str(e)}",
                }
        return {
            "channel_id": channel_id,
            "channel_name": channel["name"],
            "channel_type": channel_type,
            "status": "error",
            "message": "No URL configured for webhook channel",
        }

    elif channel_type == "slack":
        webhook_url = config.get("webhook_url", "")
        if webhook_url:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(webhook_url, json={
                        "text": f"[AgentCap Test] This is a test notification from channel '{channel['name']}'.",
                    })
                return {
                    "channel_id": channel_id,
                    "channel_name": channel["name"],
                    "channel_type": channel_type,
                    "status": "sent",
                    "message": f"Test Slack notification sent, response status: {resp.status_code}",
                }
            except Exception as e:
                return {
                    "channel_id": channel_id,
                    "channel_name": channel["name"],
                    "channel_type": channel_type,
                    "status": "error",
                    "message": f"Failed to send test Slack notification: {str(e)}",
                }
        return {
            "channel_id": channel_id,
            "channel_name": channel["name"],
            "channel_type": channel_type,
            "status": "error",
            "message": "No webhook_url configured for Slack channel",
        }

    elif channel_type == "email":
        email = config.get("email", config.get("address", ""))
        return {
            "channel_id": channel_id,
            "channel_name": channel["name"],
            "channel_type": channel_type,
            "status": "simulated",
            "message": f"Test email would be sent to '{email}'. Email delivery requires SMTP configuration.",
        }

    return {
        "channel_id": channel_id,
        "channel_name": channel["name"],
        "channel_type": channel_type,
        "status": "error",
        "message": f"Unknown channel type: {channel_type}",
    }


def _channel_row(r) -> dict:
    """Convert a notification_channels row to a response dict."""
    return {
        "id": r["id"],
        "name": r["name"],
        "channel_type": r["channel_type"],
        "config": json.loads(r["config"]) if r["config"] else {},
        "is_active": bool(r["is_active"]),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }
