"""决策 Agent 输出归一化测试：live 实测里出现过“裸提议对象”“plan 缺失”“中文枚举”。"""

import asyncio
import json

import pytest

from app.agents import decision as decision_module
from app.agents.decision import decide, normalize_plan
from app.agents.mock import mock_proposal
from app.config import settings
from app.data.scenarios import build_scenario
from app.models import ALL_AGENTS


def test_bare_proposal_object_is_wrapped():
    # 实测 DeepSeek 约一半概率直接返回单个提议，而不是 {"plan": [...]}
    data = {
        "agent": "command",
        "priority": "high",
        "target_area": "dorm_a",
        "action": "send_medical_team",
        "resource_required": {"medical_kits": 6},
        "reason": "宿舍A需求最高",
    }
    plan = normalize_plan(data)
    assert len(plan) == 1
    assert plan[0].agent == "command"
    assert plan[0].action == "send_medical_team"


def test_plan_key_aliases_are_accepted():
    for key in ("plan", "actions", "decisions", "plan_items"):
        plan = normalize_plan({key: [{"agent": "medical", "action": "idle", "target_area": ""}]})
        assert len(plan) == 1, key


def test_plan_as_mapping_is_accepted():
    data = {"plan": {"1": {"agent": "traffic", "action": "evacuate", "target_area": "dorm_a"}}}
    plan = normalize_plan(data)
    assert len(plan) == 1 and plan[0].agent == "traffic"


def test_chinese_enums_are_mapped():
    data = {"plan": [{"agent": "医疗", "action": "医疗支援", "target_area": "dorm_a"}]}
    plan = normalize_plan(data)
    assert plan[0].agent == "medical"
    assert plan[0].action == "send_medical_team"


def test_invalid_items_are_dropped_but_valid_ones_kept():
    data = {
        "plan": [
            "垃圾字符串",
            {"agent": "medical", "action": "不存在的动作", "target_area": "dorm_a"},
            {"agent": "traffic", "action": "evacuate", "target_area": "dorm_a", "priority": "high"},
        ]
    }
    plan = normalize_plan(data)
    assert len(plan) == 1
    assert plan[0].agent == "traffic"


def test_missing_optional_fields_get_defaults():
    plan = normalize_plan({"plan": [{"agent": "logistics", "action": "dispatch_supply", "target_area": "canteen"}]})
    assert plan[0].priority == "medium"


def test_empty_payload_yields_no_plan():
    assert normalize_plan({}) == []
    assert normalize_plan({"objective": "随便说说"}) == []


# ---------------------------------------------------------------- 决策重试


GOOD_PLAN = {
    "objective": "覆盖两个受困区域，保留医疗资源",
    "plan": [
        {"agent": "medical", "action": "send_medical_team", "target_area": "dorm_a",
         "priority": "high", "resource_required": {"medical_kits": 3, "volunteers": 2}},
        {"agent": "traffic", "action": "evacuate", "target_area": "dorm_a",
         "priority": "high", "resource_required": {"buses": 1}},
        {"agent": "logistics", "action": "dispatch_supply", "target_area": "dorm_b",
         "priority": "medium", "resource_required": {"volunteers": 4}},
    ],
}

BARE_ITEM = {
    "agent": "command",
    "action": "send_medical_team",
    "target_area": "dorm_a",
    "priority": "high",
    "resource_required": {"medical_kits": 6},
    "reason": "只给一条",
}


@pytest.fixture()
def live_env(monkeypatch):
    """让 settings 认为处于 live 模式，便于替换 _chat。"""
    monkeypatch.setattr(settings, "ai_mode", "auto")
    monkeypatch.setattr(settings, "api_key", "test-key")
    assert settings.mock_mode is False


def _run(monkeypatch, responses):
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    proposals = [mock_proposal(role, state) for role in ALL_AGENTS]
    queue = list(responses)

    async def fake_chat(messages, temperature=0.2, max_tokens=600):
        if not queue:
            return json.dumps(GOOD_PLAN, ensure_ascii=False)
        return queue.pop(0)

    monkeypatch.setattr(decision_module, "_chat", fake_chat)
    return state, asyncio.run(decide(state, proposals))


def test_bare_single_item_is_retried_not_accepted(monkeypatch, live_env):
    state, (resolution, trace, attempts, fallback) = _run(
        monkeypatch, [json.dumps(BARE_ITEM, ensure_ascii=False), json.dumps(GOOD_PLAN, ensure_ascii=False)]
    )
    assert fallback is False
    assert attempts == 2, "单条裸方案应被视为不完整并重试"
    assert any("不完整" in line for line in trace)
    assert len(resolution.items) >= 2


def test_all_invalid_outputs_fall_back_to_route_a(monkeypatch, live_env):
    bad = json.dumps(BARE_ITEM, ensure_ascii=False)
    state, (resolution, trace, attempts, fallback) = _run(monkeypatch, [bad, bad, bad])
    assert fallback is True
    assert attempts == 3
    assert any("回退到路线 A" in line for line in trace)
    assert resolution.items, "回退时仍应给出路线 A 的方案"


def test_rejected_plan_triggers_feedback_retry(monkeypatch, live_env):
    bad_plan = {
        "objective": "先封路",
        "plan": [
            {"agent": "security", "action": "close_road", "target_area": "2号道路", "priority": "high"},
            {"agent": "traffic", "action": "evacuate", "target_area": "dorm_a", "priority": "high",
             "resource_required": {"buses": 9}},
            {"agent": "medical", "action": "send_medical_team", "target_area": "dorm_a", "priority": "high",
             "resource_required": {"medical_kits": 9}},
        ],
    }
    _, (resolution, trace, attempts, fallback) = _run(
        monkeypatch, [json.dumps(bad_plan, ensure_ascii=False), json.dumps(GOOD_PLAN, ensure_ascii=False)]
    )
    assert fallback is False
    assert attempts == 2
    assert any("驳回" in line for line in trace)
