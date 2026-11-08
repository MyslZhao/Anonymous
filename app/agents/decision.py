"""路线 B：独立的「决策 Agent」，以整体利益最大化为目标重新编排调度方案。

默认关闭（见 README §4.3），用于与路线 A（纯规则仲裁）做同场景对比。
注意：决策 Agent 的产出同样必须再过一遍规则引擎，它没有绕过校验的特权。
"""

from __future__ import annotations

import json

from app.agents.llm import AgentError, _chat, extract_json
from app.agents.prompts import build_user_message
from app.config import settings
from app.engine.rules import OBJECTIVES, resolve
from app.engine.state import SimState
from app.models import Proposal, ResourceRequest, Resolution

DECISION_SYSTEM = """你是校园应急决策 Agent。你的目标不是复述各部门意见，而是在资源有限的约束下，
按【本轮目标函数】在救人收益、区域公平、资源消耗之间做取舍，并给出整体方案。

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
输出要求：
1. plan 必须是数组，至少 3 项行动，覆盖多个部门（医疗、转运、物资、道路管控中至少三类），
   不能只挑一条行动交差。
2. 顶层必须是 {"objective": ..., "plan": [...]}，不要把单个行动对象直接作为顶层输出。
3. 所有资源总和不得超过快照中的库存。不要输出 JSON 之外的任何文字。"""


def _mock_plan(state: SimState, proposals: list[Proposal]) -> tuple[list[Proposal], str]:
    """离线决策：完全按当前态势生成，不依赖任何具体地图 id。"""
    ranked = sorted(
        (z for z in state.zones.values() if z["trapped"] > 0),
        key=lambda z: z["trapped"],
        reverse=True,
    )
    resources = state.resources
    plan: list[Proposal] = []

    # 1) 校车：覆盖尽可能多的受困区（每区至少 1 辆）
    buses = max(0, int(resources.get("buses", 0)))
    scopes = ranked[: min(len(ranked), buses)] if buses else []
    if scopes:
        share = max(1, buses // len(scopes))
        for index, zone in enumerate(scopes):
            plan.append(
                Proposal(
                    agent="traffic" if index == 0 else "student",
                    priority="high",
                    target_area=zone["id"],
                    action="evacuate",
                    resource_required=ResourceRequest(buses=share),
                    reason=f"按覆盖优先，为{zone['name']}分配 {share} 辆校车",
                )
            )

    # 2) 医疗：医疗需求最高的区域
    if state.zones:
        target = max(state.zones.values(), key=lambda z: z["medical_need"])
        kits = min(6, int(resources.get("medical_kits", 0)))
        if kits:
            plan.append(
                Proposal(
                    agent="medical",
                    priority="high",
                    target_area=target["id"],
                    action="send_medical_team",
                    resource_required=ResourceRequest(medical_kits=kits, volunteers=4),
                    reason=f"{target['name']}医疗需求最高",
                )
            )

    # 3) 道路管控：挑一条能通过校验的路（通行中、且不是唯一通道）
    road = _safe_close_road(state)
    if road:
        plan.append(
            Proposal(
                agent="security",
                priority="high",
                target_area=road,
                action="close_road",
                resource_required=ResourceRequest(barricades=2),
                reason="道路存在通行风险，但需保留救援通行",
            )
        )

    # 4) 物资：被困次高区域，没有则人口最多的区域
    volunteers = int(resources.get("volunteers", 0))
    if volunteers >= 2 and state.zones:
        supply_target = (
            ranked[1]
            if len(ranked) > 1
            else (ranked[0] if ranked else max(state.zones.values(), key=lambda z: z["population"]))
        )
        plan.append(
            Proposal(
                agent="logistics",
                priority="medium",
                target_area=supply_target["id"],
                action="dispatch_supply",
                resource_required=ResourceRequest(volunteers=min(8, volunteers)),
                reason="保障滞留人员基本物资",
            )
        )

    # 5) 指挥：优先抢通已封闭道路，否则全校广播
    closed = [edge for edge in state.edges.values() if not edge["open"]]
    if closed and volunteers > 0:
        plan.append(
            Proposal(
                agent="command",
                priority="medium",
                target_area=closed[0]["name"],
                action="open_route",
                resource_required=ResourceRequest(volunteers=min(4, volunteers)),
                reason=f"抢通{closed[0]['name']}以恢复救援通道",
            )
        )
    elif state.zones:
        plan.append(
            Proposal(
                agent="command",
                priority="low",
                target_area=next(iter(state.zones)),
                action="notify_students",
                reason="全校广播避险通知",
            )
        )

    objective = "覆盖尽可能多的受困区域，而非把全部车辆压在一个区域"
    return plan, objective


def _safe_close_road(state: SimState) -> str | None:
    """挑一条能通过规则校验的封路目标：当前通行、且封锁后不会孤立任何受困区。"""
    risky = set(state.isolating_edges())
    candidates = [edge for edge in state.edges.values() if edge["open"] and edge["id"] not in risky]
    if not candidates:
        return None
    trapped_ids = set(state.trapped_zones())
    candidates.sort(
        key=lambda edge: (
            0 if (edge["a"] in trapped_ids or edge["b"] in trapped_ids) else 1,
            edge["id"],
        )
    )
    return candidates[0]["name"]


PLAN_KEYS = ("plan", "plans", "actions", "decisions", "plan_items")
MIN_PLAN_ITEMS = 3

AGENT_ALIASES = {
    "指挥": "command",
    "应急指挥": "command",
    "医疗": "medical",
    "保卫": "security",
    "后勤": "logistics",
    "交通": "traffic",
    "学生": "student",
    "学生事务": "student",
}

ACTION_ALIASES = {
    "医疗支援": "send_medical_team",
    "派医疗队": "send_medical_team",
    "封路": "close_road",
    "道路管控": "close_road",
    "转运": "evacuate",
    "转移": "evacuate",
    "疏散": "evacuate",
    "物资投送": "dispatch_supply",
    "抢通": "open_route",
    "道路抢通": "open_route",
    "通知": "notify_students",
    "信息发布": "notify_students",
}


def normalize_plan(data: dict) -> list[Proposal]:
    """把模型返回的“各种形状”归一成 Proposal 列表。

    实测 DeepSeek 有时不按 schema 输出，而是直接甩一个裸提议对象
    （顶层就是 agent/action/target_area），没有 plan 数组。这里全部兼容，
    并且逐条容错：某一条非法只丢这一条，不连累整份方案。
    """
    raw_items = None
    for key in PLAN_KEYS:
        if key in data:
            raw_items = data[key]
            break
    if raw_items is None:
        raw_items = [data] if "action" in data else []

    if isinstance(raw_items, dict):
        raw_items = [raw_items] if "action" in raw_items else [v for v in raw_items.values() if isinstance(v, dict)]
    if not isinstance(raw_items, list):
        raw_items = []

    proposals: list[Proposal] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        payload["agent"] = AGENT_ALIASES.get(str(payload.get("agent", "")).strip(), payload.get("agent") or "command")
        payload["action"] = ACTION_ALIASES.get(str(payload.get("action", "")).strip(), payload.get("action") or "idle")
        payload.setdefault("priority", "medium")
        try:
            proposals.append(Proposal.model_validate(payload))
        except Exception:  # 单条非法不影响其他条目
            continue
    return proposals


async def _llm_plan(
    state: SimState, proposals: list[Proposal], feedback: str = "", objective: str = "rescue"
) -> tuple[list[Proposal], str]:
    spec = OBJECTIVES.get(objective, OBJECTIVES["rescue"])
    user = (
        f"【本轮目标函数】{spec['label']}：{spec['desc']}\n"
        "请严格按这个目标函数做取舍，而不是简单复述各部门意见。\n\n"
        + build_user_message(state)
        + "\n\n【各角色原始建议】\n"
        + json.dumps([json.loads(p.model_dump_json()) for p in proposals], ensure_ascii=False, indent=2)
    )
    if feedback:
        user += f"\n\n【上一轮方案被规则引擎驳回的原因】\n{feedback}\n请修正这些问题，重新输出完整方案。"

    raw = await _chat(
        [{"role": "system", "content": DECISION_SYSTEM}, {"role": "user", "content": user}],
        temperature=0.1,
        max_tokens=1600,
    )
    data = extract_json(raw)
    plan = normalize_plan(data)
    if not plan:
        raise AgentError(f"决策 Agent 未给出可解析的计划（顶层键：{list(data)[:5]}）")
    if len(plan) < MIN_PLAN_ITEMS:
        raise AgentError(
            f"方案不完整：只给出 {len(plan)} 项行动。决策 Agent 必须综合各部门建议产出整体方案，"
            f"至少 {MIN_PLAN_ITEMS} 项行动。"
        )
    return plan, str(data.get("objective", ""))


async def decide(
    state: SimState, proposals: list[Proposal], objective: str = "rescue"
) -> tuple[Resolution, list[str], int, bool]:
    """返回 (裁决结果, 决策轨迹, 尝试次数, 是否回退到路线 A)。"""
    trace: list[str] = []
    attempts = 0
    feedback = ""
    objective = objective if objective in OBJECTIVES else "rescue"

    for attempt in range(settings.max_retry_rounds + 1):
        attempts = attempt + 1
        try:
            if settings.mock_mode:
                plan, rationale = _mock_plan(state, proposals)
            else:
                plan, rationale = await _llm_plan(state, proposals, feedback, objective)
        except Exception as exc:  # 决策 Agent 失败不影响主流程
            trace.append(f"第 {attempts} 轮决策失败：{exc}")
            feedback = (
                f"{exc}。请严格按照 system 提示中的 schema 输出，plan 至少 {MIN_PLAN_ITEMS} 项，"
                "覆盖医疗、转运、物资、道路等多个部门。"
            )
            continue

        trace.append(f"第 {attempts} 轮目标（{OBJECTIVES[objective]['label']}）：{rationale or '最大化整体救援收益'}")
        resolution = resolve(state, plan, objective)

        if not resolution.rejected:
            trace.append(f"方案通过规则校验：{len(resolution.items)} 项行动，资源余量 {resolution.ledger}")
            return resolution, trace, attempts, False

        feedback = "；".join(f"{r.agent} 的 {r.action}@{r.target_area}：{r.reason}" for r in resolution.rejected)
        trace.append(f"第 {attempts} 轮方案被规则引擎驳回（{feedback}），带反馈重试")

    trace.append("决策 Agent 未能产出可执行方案，回退到路线 A（规则仲裁）")
    return resolve(state, proposals, objective), trace, attempts, True
