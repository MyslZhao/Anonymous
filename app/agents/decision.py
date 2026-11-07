"""路线 B：独立的「决策 Agent」，以整体利益最大化为目标重新编排调度方案。

默认关闭（见 README §4.3），用于与路线 A（纯规则仲裁）做同场景对比。
注意：决策 Agent 的产出同样必须再过一遍规则引擎，它没有绕过校验的特权。
"""

from __future__ import annotations

import json

from app.agents.llm import AgentError, _chat
from app.agents.prompts import build_user_message
from app.config import settings
from app.engine.rules import resolve
from app.engine.state import SimState
from app.models import Proposal, ResourceRequest, Resolution

DECISION_SYSTEM = """你是校园应急决策 Agent。你的目标不是复述各部门意见，而是在资源有限的约束下，
最大化整体救援利益：优先覆盖受困区域数量，其次最大化获救人数，再最小化资源消耗与二次风险。

输入包含：校园状态快照 + 六个专业 Agent 的原始建议。
你必须输出 JSON：
{
  "objective": "<一句话说明你的权衡依据>",
  "plan": [
    {
      "agent": "command|medical|security|logistics|traffic|student",
      "action": "send_medical_team|close_road|evacuate|dispatch_supply|open_route|notify_students",
      "target_area": "<区域 id 或道路名称>",
      "priority": "high|medium|low",
      "resource_required": {"medical_kits":0,"volunteers":0,"buses":0,"boats":0,"barricades":0},
      "reason": "<20 字以内>"
    }
  ]
}
所有资源总和不得超过快照中的库存。不要输出 JSON 之外的任何文字。"""


def _mock_plan(state: SimState, proposals: list[Proposal]) -> tuple[list[Proposal], str]:
    """离线决策：以“覆盖更多受困区域”为目标重新分配校车。"""
    ranked = sorted(
        (z for z in state.zones.values() if z["trapped"] > 0),
        key=lambda z: z["trapped"],
        reverse=True,
    )
    plan: list[Proposal] = []
    buses = state.resources["buses"]
    if ranked:
        share = max(1, buses // min(len(ranked), buses))
        states = {"command": "command", "medical": "medical", "security": "security",
                  "logistics": "logistics", "traffic": "traffic", "student": "student"}
        for index, zone in enumerate(ranked[:buses]):
            agent = "traffic" if index == 0 else "student"
            plan.append(
                Proposal(
                    agent=states[agent],
                    priority="high",
                    target_area=zone["id"],
                    action="evacuate",
                    resource_required=ResourceRequest(buses=share),
                    reason=f"按覆盖优先，为{zone['name']}分配 {share} 辆校车",
                )
            )
        top = ranked[0]
        plan.append(
            Proposal(
                agent="medical",
                priority="high",
                target_area=top["id"],
                action="send_medical_team",
                resource_required=ResourceRequest(medical_kits=min(6, state.resources["medical_kits"]), volunteers=4),
                reason=f"{top['name']}医疗需求最高",
            )
        )
    plan.append(
        Proposal(
            agent="security",
            priority="high",
            target_area="2号道路",
            action="close_road",
            resource_required=ResourceRequest(barricades=2),
            reason="道路存在通行风险，但需保留救援通行",
        )
    )
    plan.append(
        Proposal(
            agent="logistics",
            priority="medium",
            target_area=(ranked[1]["id"] if len(ranked) > 1 else "dorm_b"),
            action="dispatch_supply",
            resource_required=ResourceRequest(volunteers=8),
            reason="保障滞留人员基本物资",
        )
    )
    plan.append(
        Proposal(
            agent="command",
            priority="medium",
            target_area="gate",
            action="notify_students",
            reason="全校广播避险通知",
        )
    )
    objective = "覆盖尽可能多的受困区域，而非把全部车辆压在一个区域"
    return plan, objective


async def _llm_plan(state: SimState, proposals: list[Proposal]) -> tuple[list[Proposal], str]:
    payload = {
        "state": state.to_dict(),
        "proposals": [json.loads(p.model_dump_json()) for p in proposals],
    }
    user = build_user_message(state) + "\n\n【各角色原始建议】\n" + json.dumps(
        payload["proposals"], ensure_ascii=False, indent=2
    )
    raw = await _chat(
        [{"role": "system", "content": DECISION_SYSTEM}, {"role": "user", "content": user}],
        temperature=0.1,
    )
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise AgentError("决策 Agent 输出缺少 JSON")
    data = json.loads(raw[start : end + 1])
    plan = [Proposal.model_validate(item) for item in data.get("plan", [])]
    if not plan:
        raise AgentError("决策 Agent 返回了空方案")
    return plan, str(data.get("objective", ""))


async def decide(
    state: SimState, proposals: list[Proposal]
) -> tuple[Resolution, list[str], int, bool]:
    """返回 (裁决结果, 决策轨迹, 尝试次数, 是否回退到路线 A)。"""
    trace: list[str] = []
    attempts = 0

    for attempt in range(settings.max_retry_rounds + 1):
        attempts = attempt + 1
        try:
            if settings.mock_mode:
                plan, objective = _mock_plan(state, proposals)
            else:
                plan, objective = await _llm_plan(state, proposals)
        except Exception as exc:  # 决策 Agent 失败不影响主流程
            trace.append(f"第 {attempts} 轮决策失败：{exc}")
            continue

        trace.append(f"第 {attempts} 轮目标：{objective or '最大化整体救援收益'}")
        resolution = resolve(state, plan)

        if not resolution.rejected:
            trace.append(f"方案通过规则校验：{len(resolution.items)} 项行动，资源余量 {resolution.ledger}")
            return resolution, trace, attempts, False

        detail = "；".join(f"{r.agent}:{r.reason}" for r in resolution.rejected)
        trace.append(f"第 {attempts} 轮方案被规则引擎驳回（{detail}），带反馈重试")

    trace.append("决策 Agent 未能产出可执行方案，回退到路线 A（规则仲裁）")
    return resolve(state, proposals), trace, attempts, True
