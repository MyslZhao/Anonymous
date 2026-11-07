"""端到端流水线测试：六角色 → 规则裁决 → 执行 → 指标 → 存档。"""

import asyncio

from app.agents.coordinator import run_simulation
from app.config import settings
from app.storage import list_runs


def test_mock_mode_is_active_in_tests():
    assert settings.mock_mode is True


def test_route_a_pipeline_end_to_end():
    payload = asyncio.run(run_simulation(seed=7, severity=3, decision_agent=False))

    assert payload["scenario"]["event"]["severity"] == 3
    assert len(payload["proposals"]) == 6
    assert {p["agent"] for p in payload["proposals"]} == {
        "command", "medical", "security", "logistics", "traffic", "student"
    }

    resolution = payload["resolution"]
    assert len(resolution["items"]) >= 3
    assert any(c["kind"] == "route" for c in resolution["conflicts"])

    result = payload["result"]
    metrics = result["metrics"]
    assert metrics["rescued"] > 0
    assert metrics["trapped_total"] > metrics["rescued"]
    assert 0 < metrics["coverage"] <= 1
    assert result["comparison"] is None

    # 反事实对照：无干预基线必须给出可对照的数字
    baseline = result["baseline"]
    assert baseline["at_risk"] > 0
    assert baseline["casualties"] > 0
    assert 0 < result["risk_covered"] <= 1
    assert result["revisions"] == []  # mock 下重申请不应空转


def test_route_b_produces_comparison_and_avoids_bus_fight():
    payload = asyncio.run(run_simulation(seed=7, severity=3, decision_agent=True))

    assert payload["decision"]["objective_mode"] is True
    assert payload["decision"]["trace"]
    comparison = payload["result"]["comparison"]
    assert comparison is not None
    assert comparison["route_a"]["rescued"] > 0
    assert comparison["route_b"]["rescued"] > 0
    # 决策 Agent 通过分散校车，避免了路线 A 的资源争抢
    assert comparison["route_b"]["conflicts"] <= comparison["route_a"]["conflicts"]


def test_runs_are_persisted():
    asyncio.run(run_simulation(seed=11, severity=2, decision_agent=False))
    runs = list_runs(limit=5)
    assert runs
    assert runs[0]["seed"] == 11
