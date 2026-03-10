import aiosqlite
import json
import httpx
from datetime import datetime, timedelta

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
                created_at TEXT NOT NULL,
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)
        await db.commit()


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
    return {
        "id": r["id"], "name": r["name"], "provider": r["provider"], "model": r["model"],
        "monthly_budget_usd": r["monthly_budget_usd"],
        "alert_threshold_pct": r["alert_threshold_pct"],
        "webhook_url": r["webhook_url"],
        "current_spend_usd": spend,
        "spend_pct": pct,
        "status": status,
        "created_at": r["created_at"],
    }


async def create_agent(db, data: dict) -> dict:
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        """INSERT INTO agents (name, provider, model, monthly_budget_usd, alert_threshold_pct, webhook_url, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (data["name"], data["provider"], data["model"],
         data["monthly_budget_usd"], data.get("alert_threshold_pct", 80.0),
         data.get("webhook_url"), now),
    )
    await db.commit()
    async with aiosqlite.connect(DB_PATH) as db2:
        db2.row_factory = aiosqlite.Row
        r = await (await db2.execute("SELECT * FROM agents WHERE id=?", (cur.lastrowid,))).fetchone()
        return _agent_row(r)


async def list_agents(db) -> list:
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute("SELECT * FROM agents ORDER BY id DESC")).fetchall()
    return [_agent_row(r) for r in rows]


async def get_agent(db, agent_id: int):
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT * FROM agents WHERE id=?", (agent_id,))).fetchone()
    return _agent_row(r) if r else None


async def update_agent(db, agent_id: int, updates: dict) -> dict | None:
    allowed = {"monthly_budget_usd", "alert_threshold_pct", "webhook_url"}
    fields = {k: v for k, v in updates.items() if k in allowed and v is not None}
    if not fields:
        return await get_agent(db, agent_id)
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [agent_id]
    cur = await db.execute(f"UPDATE agents SET {set_clause} WHERE id=?", values)
    await db.commit()
    if cur.rowcount == 0:
        return None
    return await get_agent(db, agent_id)


async def delete_agent(db, agent_id: int) -> bool:
    db.row_factory = aiosqlite.Row
    r = await (await db.execute("SELECT id FROM agents WHERE id=?", (agent_id,))).fetchone()
    if not r:
        return False
    await db.execute("DELETE FROM usage WHERE agent_id=?", (agent_id,))
    await db.execute("DELETE FROM alerts WHERE agent_id=?", (agent_id,))
    await db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
    await db.commit()
    return True


async def record_usage(db, agent_id: int, tokens_in: int, tokens_out: int,
                       cost_usd: float, request_id: str | None, metadata: dict | None) -> dict:
    now = datetime.utcnow().isoformat()
    db.row_factory = aiosqlite.Row
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
    if not agents:
        return {
            "total_agents": 0, "total_budget_usd": 0, "total_spend_usd": 0,
            "overall_utilization_pct": 0, "agents_ok": 0, "agents_warning": 0,
            "agents_capped": 0, "total_requests": 0, "top_spenders": [],
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
    return [
        {"id": r["id"], "agent_id": r["agent_id"], "agent_name": r["agent_name"],
         "alert_type": r["alert_type"], "spend_usd": r["spend_usd"],
         "budget_usd": r["budget_usd"], "spend_pct": r["spend_pct"],
         "webhook_fired": bool(r["webhook_fired"]), "created_at": r["created_at"]}
        for r in rows
    ]
