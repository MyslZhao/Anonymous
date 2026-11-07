"""调度中心：组织六角色并行决策 → 规则裁决 →（可选）决策 Agent → 执行与指标。"""

from __future__ import annotations

import asyncio

from app.agents.decision import decide
from app.agents.llm import propose
from app.agents.roles import AGENT_META
from app.config import settings
from app.data.scenarios import DEFAULT_SCENARIO, build_scenario
from app.engine.rules import resolve
from app.engine.simulator import apply_plan, snapshot, summarize_item
from app.models import ALL_AGENTS, Proposal
from app.storage import save_run
from app.engine.state import SimState

ACTION_TEXT = {
    "send_medical_team": "医疗支援",
    "close_road": "道路管控",
    "evacuate": "人员转移",
    "dispatch_supply": "物资投送",
    "open_route": "道路抢通",
    "notify_students": "信息发布",
    "idle": "待命",
}


def _target_label(state: SimState, proposal: Proposal) -> str:
    if proposal.action in ("close_road", "open_route"):
        eid = state.find_edge(proposal.target_area)
        return state.edge_name(eid) if eid else proposal.target_area
    return state.zone_name(proposal.target_area) if proposal.target_area else "—"


def proposal_payload(state: SimState, proposal: Proposal, raw: str = "") -> dict:
    meta = AGENT_META.get(proposal.agent, {})
    return {
        "agent": proposal.agent,
        "name": meta.get("name", proposal.agent),
        "color": meta.get("color", "#94a3b8"),
        "icon": meta.get("icon", "•"),
        "priority": proposal.priority,
        "action": proposal.action,
        "action_text": ACTION_TEXT.get(proposal.action, proposal.action),
        "target": _target_label(state, proposal),
        "target_id": proposal.target_area,
        "resources": {k: v for k, v in proposal.resource_required.as_dict().items() if v},
        "reason": proposal.reason,
        "raw": raw,
    }


async def _ask(role: str, state: SimState) -> tuple[Proposal, str]:
    try:
        return await propose(role, state)
    except Exception as exc:  # 单个角色失败不拖垮整轮
        return (
            Proposal(agent=role, action="idle", target_area="", reason=f"该角色本轮未给出有效建议：{exc}"),
            f"[error] {exc}",
        )


def _metrics_payload(metrics, resolution) -> dict:
    return {
        **metrics.model_dump(),
        "actions": len(resolution.items),
        "conflict_list": [c.model_dump() for c in resolution.conflicts],
    }


async def stream_simulation(
    seed: int = 7, severity: int = 3, decision_agent: bool = False, scenario: str = DEFAULT_SCENARIO
):
    """以事件流形式跑完一次模拟，供 SSE 前端逐步揭示。"""
    state = build_scenario(seed, severity, scenario)
    yield {
        "type": "scenario",
        "data": {**snapshot(state), "mode": settings.mode_label, "decision_agent": decision_agent},
    }

    tasks = [asyncio.create_task(_ask(role, state)) for role in ALL_AGENTS]
    proposals: list[Proposal] = []
    for finished in asyncio.as_completed(tasks):
        proposal, raw = await finished
        proposals.append(proposal)
        yield {"type": "proposal", "data": proposal_payload(state, proposal, raw)}
    proposals.sort(key=lambda p: ALL_AGENTS.index(p.agent))

    comparison = None
    if decision_agent:
        state_a = build_scenario(seed, severity, scenario)
        res_a = resolve(state_a, proposals)
        _, metrics_a = apply_plan(state_a, res_a)

        state_b = build_scenario(seed, severity, scenario)
        resolution, trace, attempts, fallback = await decide(state_b, proposals)
        yield {
            "type": "decision",
            "data": {"trace": trace, "attempts": attempts, "fallback": fallback, "objective_mode": True},
        }
        effects, metrics = apply_plan(state_b, resolution)
        state = state_b
        comparison = {
            "route_a": _metrics_payload(metrics_a, res_a),
            "route_b": _metrics_payload(metrics, resolution),
        }
        yield {
            "type": "resolution",
            "data": {
                "items": [summarize_item(i, state) for i in resolution.items],
                "rejected": [r.model_dump() for r in resolution.rejected],
                "conflicts": [c.model_dump() for c in resolution.conflicts],
                "ledger": resolution.ledger,
            },
        }
    else:
        resolution = resolve(state, proposals)
        effects, metrics = apply_plan(state, resolution)
        yield {
            "type": "resolution",
            "data": {
                "items": [summarize_item(i, state) for i in resolution.items],
                "rejected": [r.model_dump() for r in resolution.rejected],
                "conflicts": [c.model_dump() for c in resolution.conflicts],
                "ledger": resolution.ledger,
            },
        }

    result = {
        "type": "result",
        "data": {
            "effects": effects,
            "metrics": metrics.model_dump(),
            "comparison": comparison,
            "state": snapshot(state),
            "mode": settings.mode_label,
            "seed": seed,
            "severity": severity,
            "scenario": scenario,
            "decision_agent": decision_agent,
        },
    }
    save_run(seed, severity, decision_agent, {**result["data"], "proposals": [p.model_dump() for p in proposals]})
    yield result


async def run_simulation(
    seed: int = 7, severity: int = 3, decision_agent: bool = False, scenario: str = DEFAULT_SCENARIO
) -> dict:
    """非流式执行，返回完整结果（测试与 API 复用同一套逻辑）。"""
    events: list[dict] = []
    async for event in stream_simulation(seed, severity, decision_agent, scenario):
        events.append(event)
    payload = {
        "scenario": next((e["data"] for e in events if e["type"] == "scenario"), None),
        "proposals": [e["data"] for e in events if e["type"] == "proposal"],
        "decision": next((e["data"] for e in events if e["type"] == "decision"), None),
        "resolution": next((e["data"] for e in events if e["type"] == "resolution"), None),
        "result": next((e["data"] for e in events if e["type"] == "result"), None),
    }
    return payload
