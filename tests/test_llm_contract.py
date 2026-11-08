"""模型输出的契约层测试：解析容错、角色纠偏、区域/道路填反的拦截。"""

import pytest

from app.agents.llm import AgentError, extract_json, parse_proposal, shape_issue
from app.agents.prompts import action_schema_for
from app.data.scenarios import build_scenario
from app.models import Proposal


def test_extract_json_tolerates_code_fence_and_noise():
    raw = '好的，分析如下：\n```json\n{"agent":"medical","action":"idle"}\n```\n以上。'
    assert extract_json(raw)["agent"] == "medical"


def test_extract_json_rejects_garbage():
    with pytest.raises(AgentError):
        extract_json("我觉得应该先救人")


def test_parse_proposal_forces_role_from_caller():
    raw = '{"agent":"security","action":"idle","priority":"low"}'
    proposal = parse_proposal(raw, "medical")
    assert proposal.agent == "medical"


def test_shape_issue_flags_zone_used_as_road():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposal = Proposal(agent="security", priority="high", target_area="dorm_a", action="close_road")
    issue = shape_issue(state, proposal)
    assert issue and "道路名称" in issue


def test_shape_issue_flags_road_used_as_zone():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposal = Proposal(agent="traffic", priority="high", target_area="教学路", action="evacuate")
    issue = shape_issue(state, proposal)
    assert issue and "区域 id" in issue


@pytest.mark.parametrize(
    "action,target",
    [("close_road", "2号道路"), ("evacuate", "dorm_a"), ("send_medical_team", "clinic"), ("notify_students", "gate")],
)
def test_shape_issue_accepts_valid_targets(action, target):
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposal = Proposal(agent="medical", priority="medium", target_area=target, action=action)
    assert shape_issue(state, proposal) is None


# ---- 角色边界 ----


def test_student_cannot_request_medical_team():
    from app.agents.roles import role_issue

    issue = role_issue("student", "send_medical_team")
    assert issue and "不允许" in issue


def test_security_may_evacuate_but_not_dispatch_supply():
    from app.agents.roles import role_issue

    assert role_issue("security", "evacuate") is None
    assert role_issue("security", "dispatch_supply") is not None


@pytest.mark.parametrize("role", ["command", "medical", "security", "logistics", "traffic", "student"])
def test_mock_proposals_stay_inside_role_boundaries(role):
    from app.agents.mock import mock_proposal
    from app.agents.roles import role_issue

    state = build_scenario(seed=7, severity=3, scenario="fire")
    assert role_issue(role, mock_proposal(role, state).action) is None

def test_schema_lists_custom_map_ids():
    from app.data.maps import MapSpec
    from tests.test_maps import MAP

    state = build_scenario(seed=7, severity=3, scenario="rainstorm", map_spec=MapSpec.model_validate(MAP))
    schema = action_schema_for(state)
    assert "d1(一号宿舍)" in schema
    assert "主路" in schema
    assert "dorm_a" not in schema, "自定义地图不应再提示内置地图的区域 id"
