"""路线 A「驳回后重申请」测试：被驳回的角色要能带着原因再提一次。"""

import asyncio

from app.agents import coordinator
from app.agents.mock import mock_proposal
from app.agents.coordinator import run_simulation
from app.models import Proposal, ResourceRequest


def _fake_propose_factory(captured, revision_proposal):
    async def fake_propose(role, state, focus=None, feedback=None):
        captured.append({"role": role, "feedback": feedback})
        if feedback:
            return revision_proposal(role, state), "raw-revised"
        return mock_proposal(role, state), "[mock]"

    return fake_propose


def test_mock_mode_does_not_emit_revisions():
    """mock 的六条建议是固定输出，重申请也不会变，因此不能空转。"""
    payload = asyncio.run(run_simulation(seed=7, severity=3))
    assert payload["result"]["revisions"] == []


def test_rejected_agent_is_asked_again_with_reason(monkeypatch):
    captured: list[dict] = []

    def revised(role, state):
        return Proposal(
            agent=role,
            priority="medium",
            target_area="gate",
            action="notify_students",
            reason="申请不到校车，改为组织广播引导自行避险",
        )

    monkeypatch.setattr(coordinator, "propose", _fake_propose_factory(captured, revised))
    payload = asyncio.run(run_simulation(seed=7, severity=3))

    revisions = payload["result"]["revisions"]
    assert revisions, "校车不足导致学生事务被驳回，应触发一次重申请"
    first = revisions[0]
    assert first["agent"] == "student"
    assert "资源不足" in first["reason"]
    assert first["new"]["action"] == "notify_students"
    assert first["new"]["action"] != first["old"]["action"]

    feedbacks = [c["feedback"] for c in captured if c["feedback"]]
    assert feedbacks and "驳回" in feedbacks[0]


def test_revision_can_unlock_a_previously_rejected_action(monkeypatch):
    """学生事务改提物资投送后应当通过，方案项数比纯 mock 多。"""
    captured: list[dict] = []

    def revised(role, state):
        return Proposal(
            agent=role,
            priority="high",
            target_area="dorm_b",
            action="dispatch_supply",
            resource_required=ResourceRequest(volunteers=4),
            reason="改为请后勤协助投送物资",
        )

    monkeypatch.setattr(coordinator, "propose", _fake_propose_factory(captured, revised))
    payload = asyncio.run(run_simulation(seed=7, severity=3))
    items = payload["resolution"]["items"]
    assert any(i["agent"] == "student" for i in items), "重申请后的行动应进入最终方案"


def test_revision_loop_is_bounded(monkeypatch):
    """即使每次都被驳回，也只重试设置里的轮数，绝不死循环。"""
    captured: list[dict] = []

    def always_invalid(role, state):
        return Proposal(
            agent=role,
            priority="high",
            target_area="dorm_a",
            action="evacuate",
            resource_required=ResourceRequest(buses=20),
            reason="每次都要全部校车",
        )

    monkeypatch.setattr(coordinator, "propose", _fake_propose_factory(captured, always_invalid))
    payload = asyncio.run(run_simulation(seed=7, severity=3))
    assert len(payload["result"]["revisions"]) <= 6  # 六个角色 × 1 轮上限
