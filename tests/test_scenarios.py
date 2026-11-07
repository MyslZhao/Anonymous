"""多场景测试：四个突发事件都要能独立跑通，并保证冲突演示点不丢失。"""

import asyncio

import pytest

from app.agents.coordinator import run_simulation
from app.data.scenarios import (
    DEFAULT_SCENARIO,
    SCENARIOS,
    ScenarioOptions,
    build_scenario,
    list_scenarios,
)
from app.engine.rules import resolve
from app.agents.mock import mock_proposal
from app.models import ALL_AGENTS, Proposal, ResourceRequest

SCENARIO_IDS = list(SCENARIOS)


def test_registry_lists_all_scenarios():
    items = list_scenarios()
    assert {item["id"] for item in items} == set(SCENARIO_IDS)
    assert DEFAULT_SCENARIO in SCENARIOS
    for item in items:
        assert item["name"] and item["summary"]


@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_each_scenario_builds_with_incident_data(scenario):
    state = build_scenario(seed=7, severity=3, scenario=scenario)
    assert state.event["id"] == scenario
    assert state.event["hazard_label"]
    assert state.total_trapped() > 0
    assert state.event["title"]


@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_each_scenario_produces_executable_plan(scenario):
    state = build_scenario(seed=7, severity=3, scenario=scenario)
    resolution = resolve(state, [mock_proposal(role, state) for role in ALL_AGENTS])
    assert resolution.items, f"{scenario} 没有任何行动通过裁决"
    assert resolution.conflicts, f"{scenario} 应至少出现一处冲突"


@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_each_scenario_end_to_end(scenario):
    payload = asyncio.run(run_simulation(seed=7, severity=3, scenario=scenario))
    assert payload["scenario"]["event"]["id"] == scenario
    assert len(payload["proposals"]) == 6
    metrics = payload["result"]["metrics"]
    assert metrics["trapped_total"] > 0
    assert metrics["conflicts"] >= 1
    assert payload["result"]["scenario"] == scenario


@pytest.mark.parametrize("scenario", ["fire", "food_safety"])
def test_high_severity_blocks_the_only_route(scenario):
    """火灾与食物中毒场景同样设计了“唯一通道”冲突前提。"""
    state = build_scenario(seed=7, severity=3, scenario=scenario)
    assert state.path_exists("dorm_a", "clinic")
    assert state.path_exists("dorm_a", "clinic", {"e_road2"}) is False


def test_unknown_scenario_raises():
    with pytest.raises(ValueError):
        build_scenario(seed=7, severity=3, scenario="earthquake")


def test_scenario_is_deterministic_across_calls():
    a = build_scenario(seed=3, severity=2, scenario="fire")
    b = build_scenario(seed=3, severity=2, scenario="fire")
    assert a.to_dict()["zones"] == b.to_dict()["zones"]


def test_assign_focus_spreads_agents_across_zones():
    from app.agents.coordinator import assign_focus

    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    focus = assign_focus(state)
    assert set(focus) == {"traffic", "student", "medical", "security", "logistics"}
    assert focus["traffic"] != focus["student"], "交通与学生事务不应被分到同一个重点区域"
    assert "dorm_a" in focus["traffic"]
    assert "（" in focus["medical"]


# ---- 可调规模与运力 ----


def test_population_scale_multiplies_trapped_and_population():
    base = build_scenario(seed=7, severity=3, scenario="rainstorm")
    doubled = build_scenario(seed=7, severity=3, scenario="rainstorm", options=ScenarioOptions(population_scale=2.0))
    assert doubled.total_trapped() == pytest.approx(base.total_trapped() * 2, rel=0.02)
    assert doubled.event["population_scale"] == 2.0
    for zone in doubled.zones.values():
        base_zone = base.zones[zone["id"]]
        assert zone["population"] >= base_zone["population"]


def test_vehicle_override_changes_resources_only():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm", options=ScenarioOptions(buses=6, boats=3))
    assert state.resources["buses"] == 6
    assert state.resources["boats"] == 3
    # 其他资源保持场景默认
    assert state.resources["medical_kits"] == build_scenario(seed=7, severity=3).resources["medical_kits"]


def test_options_are_clamped():
    opts = ScenarioOptions.clamp(population_scale=99, buses=999, boats=-5)
    assert opts.population_scale == 5.0
    assert opts.buses == 20
    assert opts.boats == 0


def test_more_buses_relieves_bus_contention_when_both_sides_ask_less():
    """运力充足时，两个撤离请求可以同时通过——这正是“智能调度”演示的关键。"""
    state = build_scenario(seed=7, severity=3, scenario="rainstorm", options=ScenarioOptions(buses=6))
    proposals = [
        Proposal(
            agent="traffic",
            priority="high",
            target_area="dorm_a",
            action="evacuate",
            resource_required=ResourceRequest(buses=2),
        ),
        Proposal(
            agent="student",
            priority="high",
            target_area="dorm_b",
            action="evacuate",
            resource_required=ResourceRequest(buses=2),
        ),
    ]
    resolution = resolve(state, proposals)
    assert len(resolution.items) == 2
    assert not resolution.rejected
