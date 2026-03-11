"""Smoke tests for AgentCap — validates core functionality without external deps."""
import asyncio
import os
import sys

# Ensure imports work from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import (
    init_db, create_agent, get_agent, delete_agent,
    record_usage, forecast_budget, provider_breakdown,
    export_usage_csv, adjust_budget, get_dashboard,
)
import aiosqlite

DB_PATH = "agentcap_test.db"


async def run():
    # Override DB path for testing
    import engine
    engine.DB_PATH = DB_PATH

    try:
        await init_db()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # 1. Create agents
            a1 = await create_agent(db, {
                "name": "gpt4-agent", "provider": "openai",
                "model": "gpt-4", "monthly_budget_usd": 100.0,
            })
            assert a1["id"] == 1
            assert a1["status"] == "ok"
            assert a1["spend_pct"] == 0
            print("[PASS] create_agent")

            a2 = await create_agent(db, {
                "name": "claude-agent", "provider": "anthropic",
                "model": "claude-3", "monthly_budget_usd": 50.0,
            })
            assert a2["provider"] == "anthropic"
            print("[PASS] create second agent")

            # 2. Record usage
            u = await record_usage(db, 1, 1000, 500, 0.05, "req-1", {"task": "test"})
            assert u["cost_usd"] == 0.05
            agent = await get_agent(db, 1)
            assert agent["current_spend_usd"] == 0.05
            print("[PASS] record_usage")

            # 3. Forecast (with minimal data)
            fc = await forecast_budget(db, 1)
            assert fc["agent_name"] == "gpt4-agent"
            assert fc["trend"] == "stable"
            print("[PASS] forecast_budget")

            # 4. Provider breakdown
            pb = await provider_breakdown(db)
            assert pb["total_providers"] == 2
            providers = {p["provider"] for p in pb["providers"]}
            assert "openai" in providers
            assert "anthropic" in providers
            print("[PASS] provider_breakdown")

            # 5. CSV export
            csv = await export_usage_csv(db, 1)
            assert "req-1" in csv
            assert "tokens_in" in csv
            print("[PASS] export_usage_csv")

            # 6. Budget adjustment
            adj = await adjust_budget(db, 1, 200.0, "Scaling up usage")
            assert adj["old_budget_usd"] == 100.0
            assert adj["new_budget_usd"] == 200.0
            print("[PASS] adjust_budget")

            # 7. Dashboard
            dash = await get_dashboard(db)
            assert dash["total_agents"] == 2
            assert dash["total_requests"] >= 1
            print("[PASS] dashboard")

            # 8. Delete agent (cascade)
            ok = await delete_agent(db, 2)
            assert ok is True
            assert await get_agent(db, 2) is None
            print("[PASS] delete_agent cascade")

        print("\n=== All smoke tests passed ===")

    finally:
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)


if __name__ == "__main__":
    asyncio.run(run())
