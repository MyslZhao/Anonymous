"""规则引擎：把六份 Agent 建议裁决成一份可执行方案。

核心原则：AI 只提议，这里判定“资源是否真的够、路是否真的通、行动是否冲突”。
规则引擎从不调用大模型，因此结果完全可复现、可测试。
"""

from __future__ import annotations

from app.models import Conflict, PlanItem, Proposal, Rejection, Resolution
from app.engine.state import SimState

PRIORITY_WEIGHT: dict[str, int] = {"high": 3, "medium": 2, "low": 1}
BUS_CAPACITY = 25
SUPPLY_PER_VOLUNTEER = 10
MEDICAL_PER_KIT = 3
SHELTERS = ("gym", "gate")
DEPOT = "teaching"  # 物资集散点


def benefit_estimate(state: SimState, proposal: Proposal) -> float:
    """估算一次行动的收益（人数或风险降低），用于排序与冲突裁决。"""
    zone = state.zones.get(proposal.target_area)
    req = proposal.resource_required
    if proposal.action == "evacuate":
        return float(min(zone["trapped"], req.buses * BUS_CAPACITY)) if zone else 0.0
    if proposal.action == "send_medical_team":
        if not zone:
            return 0.0
        capacity = req.medical_kits * MEDICAL_PER_KIT + req.volunteers * 2
        return float(min(zone["medical_need"], capacity))
    if proposal.action == "dispatch_supply":
        return float(min(zone["population"], req.volunteers * SUPPLY_PER_VOLUNTEER)) if zone else 0.0
    if proposal.action == "close_road":
        return 12.0
    if proposal.action == "open_route":
        return 18.0
    if proposal.action == "notify_students":
        return 5.0
    return 0.0


def score(proposal: Proposal, benefit: float) -> float:
    return PRIORITY_WEIGHT[proposal.priority] * 100 + benefit


def _target_label(state: SimState, proposal: Proposal) -> str:
    if proposal.action in ("close_road", "open_route"):
        eid = state.find_edge(proposal.target_area)
        return state.edge_name(eid) if eid else proposal.target_area
    return state.zone_name(proposal.target_area)


def _resource_shortfall(ledger: dict[str, int], request: dict[str, int]) -> list[str]:
    short: list[str] = []
    for key, need in request.items():
        if need and ledger.get(key, 0) < need:
            short.append(f"{key} 需要 {need} 剩余 {ledger.get(key, 0)}")
    return short


def _dedupe(proposals: list[Proposal]) -> tuple[list[Proposal], list[Conflict]]:
    """同一角色重复提同一行动只留收益最高的一条。"""
    best: dict[tuple[str, str], Proposal] = {}
    conflicts: list[Conflict] = []
    for proposal in proposals:
        key = (proposal.action, proposal.target_area)
        current = best.get(key)
        if current is None:
            best[key] = proposal
            continue
        keeper, dropped = (
            (proposal, current)
            if PRIORITY_WEIGHT[proposal.priority] > PRIORITY_WEIGHT[current.priority]
            else (current, proposal)
        )
        best[key] = keeper
        conflicts.append(
            Conflict(
                kind="duplicate",
                parties=[keeper.agent, dropped.agent],
                detail=f"两个角色对「{keeper.target_area}」提出了相同的 {keeper.action}",
                resolution=f"合并为一条，保留 {keeper.agent}（优先级更高）",
            )
        )
    return list(best.values()), conflicts


def resolve(state: SimState, proposals: list[Proposal]) -> Resolution:
    """裁决入口。完全确定性：同样的输入必然得到同样的输出。"""
    conflicts: list[Conflict] = []
    rejected: list[Rejection] = []
    items: list[PlanItem] = []
    ledger = dict(state.resources)
    closed_extra: set[str] = set()

    candidates = [p for p in proposals if p.action != "idle"]
    for p in proposals:
        if p.action == "idle":
            rejected.append(
                Rejection(agent=p.agent, action=p.action, target_area=p.target_area, reason="未提出可执行行动")
            )

    candidates, dup_conflicts = _dedupe(candidates)
    conflicts.extend(dup_conflicts)

    ranked = sorted(
        candidates,
        key=lambda p: (score(p, benefit_estimate(state, p)), p.agent),
        reverse=True,
    )

    for proposal in ranked:
        benefit = benefit_estimate(state, proposal)
        request = proposal.resource_required.as_dict()
        label = _target_label(state, proposal)

        # ---- 1. 资源校验 ----
        short = _resource_shortfall(ledger, request)
        if short:
            rejected.append(
                Rejection(
                    agent=proposal.agent,
                    action=proposal.action,
                    target_area=proposal.target_area,
                    reason="资源不足：" + "；".join(short),
                )
            )
            competitors = [i.agent for i in items if benefit_estimate(state, _as_proposal(i)) > 0]
            conflicts.append(
                Conflict(
                    kind="resource",
                    parties=[proposal.agent] + competitors,
                    detail=f"{proposal.agent} 申请 {label} 的资源超过剩余库存（{'；'.join(short)}）",
                    resolution=f"按收益排序后该申请未被满足，{proposal.agent} 需改用替代方案",
                )
            )
            continue

        # ---- 2. 行动可行性 ----
        if proposal.action in ("send_medical_team", "dispatch_supply"):
            origin = "clinic" if proposal.action == "send_medical_team" else DEPOT
            if proposal.target_area not in state.zones:
                rejected.append(
                    Rejection(agent=proposal.agent, action=proposal.action, target_area=proposal.target_area, reason="目标区域不存在")
                )
                continue
            if not state.path_exists(origin, proposal.target_area, closed_extra):
                rejected.append(
                    Rejection(
                        agent=proposal.agent,
                        action=proposal.action,
                        target_area=proposal.target_area,
                        reason=f"路径阻断：{origin} 无法到达 {label}",
                    )
                )
                continue

        if proposal.action in ("close_road", "open_route"):
            eid = state.find_edge(proposal.target_area)
            if eid is None:
                rejected.append(
                    Rejection(agent=proposal.agent, action=proposal.action, target_area=proposal.target_area, reason="道路不存在")
                )
                continue
            edge = state.edges[eid]
            if proposal.action == "open_route" and edge["open"]:
                rejected.append(
                    Rejection(agent=proposal.agent, action=proposal.action, target_area=proposal.target_area, reason=f"{edge['name']} 本就通行")
                )
                continue
            if proposal.action == "close_road":
                if not edge["open"]:
                    rejected.append(
                        Rejection(agent=proposal.agent, action=proposal.action, target_area=proposal.target_area, reason=f"{edge['name']} 已封闭")
                    )
                    continue
                isolation = state.isolating_edges(closed_extra)
                if eid in isolation or _would_isolate(state, eid, closed_extra):
                    conflicts.append(
                        Conflict(
                            kind="route",
                            parties=[proposal.agent, "medical", "traffic"],
                            detail=f"封锁 {edge['name']} 将切断被困区域的唯一救援通道",
                            resolution="裁决：不中断通行，改为设警戒点并由保卫科现场疏导",
                        )
                    )
                    # 降级执行为“设警戒点”，仍消耗 1 个路障
                    if ledger.get("barricades", 0) >= 1:
                        ledger["barricades"] -= 1
                        items.append(
                            PlanItem(
                                agent=proposal.agent,
                                action="close_road",
                                target_area=eid,
                                priority=proposal.priority,
                                resources={"barricades": 1},
                                reason=proposal.reason,
                                modified=True,
                                note=f"{edge['name']} 不封路，改设警戒点",
                            )
                        )
                    continue
                closed_extra.add(eid)

        if proposal.action == "evacuate":
            if proposal.target_area not in state.zones:
                rejected.append(
                    Rejection(agent=proposal.agent, action=proposal.action, target_area=proposal.target_area, reason="目标区域不存在")
                )
                continue
            reachable = any(state.path_exists(proposal.target_area, s, closed_extra) for s in SHELTERS)
            if not reachable:
                rejected.append(
                    Rejection(
                        agent=proposal.agent,
                        action=proposal.action,
                        target_area=proposal.target_area,
                        reason=f"路径阻断：{label} 无法到达任何避难所",
                    )
                )
                continue

        # ---- 3. 接受并扣减资源 ----
        for key, need in request.items():
            if need:
                ledger[key] -= need
        items.append(
            PlanItem(
                agent=proposal.agent,
                action=proposal.action,
                target_area=proposal.target_area,
                priority=proposal.priority,
                resources={k: v for k, v in request.items() if v},
                reason=proposal.reason,
            )
        )

    return Resolution(items=items, rejected=rejected, conflicts=conflicts, ledger=ledger)


def _would_isolate(state: SimState, eid: str, closed_extra: set[str]) -> bool:
    blocked = set(closed_extra) | {eid}
    for zid in state.trapped_zones():
        before = any(state.path_exists(zid, s) for s in SHELTERS) or state.path_exists(zid, "clinic")
        after = any(state.path_exists(zid, s, blocked) for s in SHELTERS) or state.path_exists(
            zid, "clinic", blocked
        )
        if before and not after:
            return True
    return False


def _as_proposal(item: PlanItem) -> Proposal:
    """把已接受的方案项还原成 Proposal，用于收益比较。"""
    from app.models import ResourceRequest

    return Proposal(
        agent=item.agent,
        priority=item.priority,
        target_area=item.target_area,
        action=item.action,
        resource_required=ResourceRequest(**item.resources),
        reason=item.reason,
    )
