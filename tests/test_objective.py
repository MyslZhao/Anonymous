"""目标函数、路线可视化、多轮推演测试。"""

import asyncio

import pytest

from app.agents.coordinator import run_simulation
from app.data.scenarios import build_scenario
from app.engine.rules import OBJECTIVES, resolve
from app.models import Proposal, ResourceRequest


def _contested_proposals():
    """dorm_a 被两个角色盯上，dorm_b 只有一个；校车只够一方。"""
    return [
        Proposal(
            agent="traffic", priority="high", target_area="dorm_a", action="evacuate",
            resource_required=ResourceRequest(buses=2), reason="宿舍A优先",
        ),
        Proposal(
            agent="student", priority="high", target_area="dorm_b", action="evacuate",
            resource_required=ResourceRequest(buses=2), reason="宿舍B请求支援",
        ),
        Proposal(
            agent="medical", priority="high", target_area="dorm_a", action="send_medical_team",
            resource_required=ResourceRequest(medical_kits=2), reason="宿舍A医疗需求",
        ),
    ]


def test_objectives_are_registered():
    assert set(OBJECTIVES) == {"rescue", "fairness", "economy"}
    for spec in OBJECTIVES.values():
        assert spec["label"] and spec["desc"]


def test_rescue_objective_concentrates_on_the_worst_zone():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    resolution = resolve(state, _contested_proposals(), "rescue")
    evacuated = [i.target_area for i in resolution.items if i.action == "evacuate"]
    assert evacuated == ["dorm_a"]


def test_fairness_objective_prefers_the_less_contested_zone():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    resolution = resolve(state, _contested_proposals(), "fairness")
    evacuated = [i.target_area for i in resolution.items if i.action == "evacuate"]
    assert evacuated == ["dorm_b"]


def test_economy_objective_penalizes_resource_heavy_actions():
    from app.engine.rules import score

    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    expensive = Proposal(
        agent="traffic", priority="high", target_area="dorm_a", action="evacuate",
        resource_required=ResourceRequest(buses=3), reason="转移",
    )
    cheap = Proposal(agent="command", priority="high", target_area="gate", action="notify_students")
    gap_rescue = score(expensive, 60, "rescue") - score(cheap, 5, "rescue")
    gap_economy = score(expensive, 60, "economy") - score(cheap, 5, "economy")
    assert gap_economy < gap_rescue, "economy 目标应缩小昂贵行动的优势"
    assert score(cheap, 5, "economy") == score(cheap, 5, "rescue"), "零成本行动不受目标函数影响"


def test_unknown_objective_falls_back_to_rescue():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    a = resolve(state, _contested_proposals(), "nonsense")
    b = resolve(state, _contested_proposals(), "rescue")
    assert [i.action for i in a.items] == [i.action for i in b.items]


# ---- 路线可视化 ----


def test_evacuation_carries_a_route():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposal = Proposal(
        agent="traffic", priority="high", target_area="dorm_a", action="evacuate",
        resource_required=ResourceRequest(buses=1),
    )
    resolution = resolve(state, [proposal])
    path = resolution.items[0].path
    assert path, "疏散行动必须带上途经道路"
    assert "2号道路" in path, "宿舍A 唯一通道是 2 号道路"
    assert path[-1] in {"东环路"} or path[0] == "2号道路"


def test_medical_route_starts_from_clinic():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposal = Proposal(
        agent="medical", priority="high", target_area="dorm_a", action="send_medical_team",
        resource_required=ResourceRequest(medical_kits=2),
    )
    resolution = resolve(state, [proposal])
    assert resolution.items[0].path[0] == "医务路"


def test_self_path_has_no_edges():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    assert state.find_path("clinic", "clinic") == []


# ---- 多轮推演 ----


def test_two_rounds_accumulate_metrics():
    once = asyncio.run(run_simulation(seed=7, severity=3, rounds=1))
    twice = asyncio.run(run_simulation(seed=7, severity=3, rounds=2))
    assert len(once["result"]["rounds"]) == 1
    assert len(twice["result"]["rounds"]) == 2
    assert twice["result"]["metrics"]["rescued"] >= once["result"]["metrics"]["rescued"]
    assert {e["round"] for e in twice["result"]["effects"]} == {1, 2}


def test_rounds_are_clamped():
    payload = asyncio.run(run_simulation(seed=7, severity=3, rounds=9))
    assert len(payload["result"]["rounds"]) == 3


def test_each_round_replans_with_worse_conditions():
    payload = asyncio.run(run_simulation(seed=7, severity=3, rounds=2))
    proposals = payload["proposals"]
    assert len(proposals) == 12, "两轮各六条建议"
    assert {p["round"] for p in proposals} == {1, 2}


def test_objective_is_reported_in_result():
    payload = asyncio.run(run_simulation(seed=7, severity=3, objective="fairness"))
    assert payload["result"]["objective"] == "fairness"
    assert payload["result"]["objective_label"] == "区域公平"
    assert payload["scenario"]["objective"] == "fairness"
