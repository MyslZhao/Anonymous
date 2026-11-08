"""规则引擎：把六份 Agent 建议裁决成一份可执行方案。

核心原则：AI 只提议，这里判定“资源是否真的够、路是否真的通、行动是否冲突”。
规则引擎从不调用大模型，因此结果完全可复现、可测试。
"""

from __future__ import annotations

from typing import Literal

from app.models import Conflict, PlanItem, Proposal, Rejection, Resolution
from app.engine.state import SimState

PRIORITY_WEIGHT: dict[str, int] = {"high": 3, "medium": 2, "low": 1}
BUS_CAPACITY = 25
BOAT_CAPACITY = 10  # 每艘冲锋舟单轮转移人数
SUPPLY_PER_VOLUNTEER = 10
MEDICAL_PER_KIT = 3

Objective = Literal["rescue", "fairness", "economy"]
OBJECTIVES: dict[str, dict] = {
    "rescue": {"label": "救人优先", "desc": "最大化获救与处置人数，不敏感于资源消耗"},
    "fairness": {"label": "区域公平", "desc": "避免资源集中在单一区域，尽量让每个受困区域都被处置"},
    "economy": {"label": "资源最省", "desc": "在保住基本救援的前提下，优先低成本行动"},
}

# 各类资源的“调度代价”权重，用于 economy 目标
RESOURCE_COST: dict[str, float] = {
    "medical_kits": 1.0,
    "volunteers": 0.4,
    "buses": 3.0,
    "boats": 3.0,
    "barricades": 0.4,
}


def request_cost(request: dict[str, int]) -> float:
    return sum(RESOURCE_COST.get(key, 1.0) * value for key, value in request.items())


def benefit_estimate(state: SimState, proposal: Proposal) -> float:
    """估算一次行动的收益（人数或风险降低），用于排序与冲突裁决。"""
    zone = state.zones.get(proposal.target_area)
    req = proposal.resource_required
    if proposal.action == "evacuate":
        capacity = req.buses * BUS_CAPACITY + req.boats * BOAT_CAPACITY
        return float(min(zone["trapped"], capacity)) if zone else 0.0
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


def score(proposal: Proposal, benefit: float, objective: str = "rescue", zone_load: int = 0) -> float:
    """排序分 = 优先级权重 + 收益 + 目标函数调整。

    目标函数只改变“同等条件下谁先上”，不改变可行性判定：
    无论选哪个目标，资源超发、道路阻断这些硬约束照样拦截。
    """
    base = PRIORITY_WEIGHT[proposal.priority] * 100 + benefit
    cost = request_cost(proposal.resource_required.as_dict())
    if objective == "economy":
        return base - 1.6 * cost
    if objective == "fairness":
        # 同一区域被多个角色盯上时降权，鼓励把资源铺开
        return base - 16.0 * max(0, zone_load - 1)
    return base - 0.5 * cost  # rescue：资源只是轻微代价


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


def resolve(state: SimState, proposals: list[Proposal], objective: str = "rescue") -> Resolution:
    """裁决入口。完全确定性：同样的输入（含 objective）必然得到同样的输出。"""
    objective = objective if objective in OBJECTIVES else "rescue"
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

    zone_load: dict[str, int] = {}
    for candidate in candidates:
        zone_load[candidate.target_area] = zone_load.get(candidate.target_area, 0) + 1

    ranked = sorted(
        candidates,
        key=lambda p: (
            score(p, benefit_estimate(state, p), objective, zone_load.get(p.target_area, 1)),
            p.agent,
        ),
        reverse=True,
    )

    for proposal in ranked:
        # 广播通知不占用实体资源，模型给 notify_students 挂资源一律忽略
        request = {} if proposal.action == "notify_students" else proposal.resource_required.as_dict()
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
            origin = state.medical_zone() if proposal.action == "send_medical_team" else state.depot_zone()
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
            reachable = any(state.path_exists(proposal.target_area, s, closed_extra) for s in state.shelter_zones())
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
                path=_plan_path(state, proposal, closed_extra),
            )
        )

    return Resolution(items=items, rejected=rejected, conflicts=conflicts, ledger=ledger)


def _plan_path(state: SimState, proposal: Proposal, closed_extra: set[str]) -> list[str]:
    """给行动算出实际路线（边名序列），供前端在地图上画出来。"""
    if proposal.action in ("close_road", "open_route"):
        eid = state.find_edge(proposal.target_area)
        return [state.edge_name(eid)] if eid else []
    if proposal.action == "send_medical_team":
        medical = state.medical_zone()
        return _edge_names(state, state.find_path(medical, proposal.target_area, closed_extra)) if medical else []
    if proposal.action == "dispatch_supply":
        depot = state.depot_zone()
        return _edge_names(state, state.find_path(depot, proposal.target_area, closed_extra)) if depot else []
    if proposal.action == "evacuate":
        for shelter in state.shelter_zones():
            edges = state.find_path(proposal.target_area, shelter, closed_extra)
            if edges:
                return _edge_names(state, edges)
    return []


def _edge_names(state: SimState, edge_ids: list[str]) -> list[str]:
    return [state.edge_name(eid) for eid in edge_ids]


def _would_isolate(state: SimState, eid: str, closed_extra: set[str]) -> bool:
    blocked = set(closed_extra) | {eid}
    care = state.care_zones()
    if not care:
        return False
    for zid in state.trapped_zones():
        before = any(state.path_exists(zid, zone) for zone in care)
        after = any(state.path_exists(zid, zone, blocked) for zone in care)
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
