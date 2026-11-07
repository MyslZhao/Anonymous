"""规则引擎测试：资源不足要驳回、封路要降级、重复提议要合并。"""

from app.agents.mock import mock_proposal
from app.data.scenarios import build_scenario
from app.engine.rules import benefit_estimate, resolve
from app.models import ALL_AGENTS, Proposal, ResourceRequest


def _proposals(state):
    return [mock_proposal(role, state) for role in ALL_AGENTS]


def test_mock_proposals_cover_all_roles():
    state = build_scenario(seed=7, severity=3)
    assert {p.agent for p in _proposals(state)} == set(ALL_AGENTS)


def test_bus_shortage_is_rejected_and_logged_as_conflict():
    state = build_scenario(seed=7, severity=3)
    resolution = resolve(state, _proposals(state))
    # 交通和学生事务都要 2 辆车，但全校只有 2 辆
    bus_rejections = [r for r in resolution.rejected if "buses" in r.reason]
    assert bus_rejections, "应有一条校车不足的驳回"
    assert any(c.kind == "resource" for c in resolution.conflicts)


def test_closing_only_rescue_route_is_downgraded():
    state = build_scenario(seed=7, severity=3)
    resolution = resolve(state, _proposals(state))
    route_conflicts = [c for c in resolution.conflicts if c.kind == "route"]
    assert route_conflicts, "封锁 2 号道路必须触发路径冲突"
    modified = [i for i in resolution.items if i.modified]
    assert modified and modified[0].note


def test_accepted_items_never_exceed_inventory():
    state = build_scenario(seed=7, severity=3)
    resolution = resolve(state, _proposals(state))
    used = {k: 0 for k in state.resources}
    for item in resolution.items:
        for key, value in item.resources.items():
            used[key] += value
    for key, value in used.items():
        assert value <= state.resources[key], f"{key} 超发：{value} > {state.resources[key]}"


def test_duplicates_are_merged():
    state = build_scenario(seed=7, severity=3)
    proposals = [
        Proposal(agent="medical", priority="high", target_area="dorm_a", action="notify_students"),
        Proposal(agent="command", priority="medium", target_area="dorm_a", action="notify_students"),
    ]
    resolution = resolve(state, proposals)
    assert len([i for i in resolution.items if i.action == "notify_students"]) == 1
    assert any(c.kind == "duplicate" for c in resolution.conflicts)


def test_idle_proposal_is_rejected_not_executed():
    state = build_scenario(seed=7, severity=1)
    resolution = resolve(state, [Proposal(agent="traffic", action="idle")])
    assert resolution.items == []
    assert resolution.rejected


def test_resource_insufficient_proposal_rejected():
    state = build_scenario(seed=7, severity=3)
    huge = Proposal(
        agent="traffic",
        priority="high",
        target_area="dorm_a",
        action="evacuate",
        resource_required=ResourceRequest(buses=20),
    )
    resolution = resolve(state, [huge])
    assert not resolution.items
    assert "资源不足" in resolution.rejected[0].reason


def test_benefit_estimate_prefers_larger_rescue():
    state = build_scenario(seed=7, severity=3)
    small = Proposal(agent="traffic", target_area="dorm_a", action="evacuate",
                     resource_required=ResourceRequest(buses=1))
    big = Proposal(agent="traffic", target_area="dorm_a", action="evacuate",
                   resource_required=ResourceRequest(buses=2))
    assert benefit_estimate(state, big) > benefit_estimate(state, small)


# ---- 以下用例针对 live 模式下才会走到的驳回分支（mock 很难覆盖） ----


def test_close_already_closed_road_is_rejected():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposal = Proposal(agent="security", priority="high", target_area="教学路", action="close_road")
    resolution = resolve(state, [proposal])
    assert not resolution.items
    assert resolution.rejected[0].reason.endswith("已封闭")


def test_open_already_open_road_is_rejected():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposal = Proposal(agent="traffic", priority="high", target_area="主干道", action="open_route")
    resolution = resolve(state, [proposal])
    assert not resolution.items
    assert resolution.rejected[0].reason.endswith("本就通行")


def test_unknown_road_is_rejected():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposal = Proposal(agent="security", priority="high", target_area="不存在的路", action="close_road")
    resolution = resolve(state, [proposal])
    assert not resolution.items
    assert resolution.rejected[0].reason == "道路不存在"


def test_unknown_zone_is_rejected():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposal = Proposal(
        agent="medical",
        priority="high",
        target_area="dorm_z",
        action="send_medical_team",
        resource_required=ResourceRequest(medical_kits=1),
    )
    resolution = resolve(state, [proposal])
    assert not resolution.items
    assert resolution.rejected[0].reason == "目标区域不存在"


def test_notify_students_ignores_requested_resources():
    """广播通知不占资源，模型给它挂志愿者也不该被驳回。"""
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposal = Proposal(
        agent="command",
        priority="high",
        target_area="gate",
        action="notify_students",
        resource_required=ResourceRequest(volunteers=30, buses=5),
    )
    resolution = resolve(state, [proposal])
    assert len(resolution.items) == 1
    assert not resolution.items[0].resources
    assert resolution.ledger == state.resources


def test_evacuate_from_isolated_zone_is_rejected():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    state.edges["e_road2"].update(open=False, reason="测试用：切断唯一通道")
    proposal = Proposal(
        agent="traffic",
        priority="high",
        target_area="dorm_a",
        action="evacuate",
        resource_required=ResourceRequest(buses=1),
    )
    resolution = resolve(state, [proposal])
    assert not resolution.items
    assert "路径阻断" in resolution.rejected[0].reason


def test_medical_team_to_isolated_zone_is_rejected():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    state.edges["e_road2"].update(open=False, reason="测试用：切断唯一通道")
    proposal = Proposal(
        agent="medical",
        priority="high",
        target_area="dorm_a",
        action="send_medical_team",
        resource_required=ResourceRequest(medical_kits=2),
    )
    resolution = resolve(state, [proposal])
    assert not resolution.items
    assert "路径阻断" in resolution.rejected[0].reason


def test_supply_to_isolated_zone_is_rejected():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    state.edges["e_road2"].update(open=False, reason="测试用：切断唯一通道")
    proposal = Proposal(
        agent="logistics",
        priority="high",
        target_area="dorm_a",
        action="dispatch_supply",
        resource_required=ResourceRequest(volunteers=2),
    )
    resolution = resolve(state, [proposal])
    assert not resolution.items
    assert "路径阻断" in resolution.rejected[0].reason
