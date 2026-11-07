"""模拟引擎：把裁决后的方案作用到校园状态上，产出可量化结果。"""

from __future__ import annotations

from app.engine.rules import BUS_CAPACITY, MEDICAL_PER_KIT, SUPPLY_PER_VOLUNTEER
from app.engine.state import SimState
from app.models import Metrics, PlanItem, Resolution


def apply_plan(state: SimState, resolution: Resolution) -> tuple[list[dict], Metrics]:
    """执行方案，原地更新状态，返回逐条效果与汇总指标。"""
    trapped_total = state.total_trapped()
    effects: list[dict] = []
    rescued = stabilized = supported = 0
    buses_used = 0

    for item in resolution.items:
        if item.action == "evacuate":
            zone = state.zones.get(item.target_area)
            if not zone:
                continue
            buses = item.resources.get("buses", 0)
            moved = min(zone["trapped"], buses * BUS_CAPACITY)
            zone["trapped"] -= moved
            zone["evacuated"] += moved
            rescued += moved
            buses_used += buses
            effects.append(
                {
                    "action": item.action,
                    "zone": zone["name"],
                    "text": f"转移 {moved} 人至体育馆（{buses} 辆校车）",
                    "value": moved,
                }
            )
        elif item.action == "send_medical_team":
            zone = state.zones.get(item.target_area)
            if not zone:
                continue
            capacity = item.resources.get("medical_kits", 0) * MEDICAL_PER_KIT + item.resources.get(
                "volunteers", 0
            )
            healed = min(zone["medical_need"], capacity)
            zone["medical_need"] = max(0, zone["medical_need"] - healed)
            stabilized += healed
            effects.append(
                {
                    "action": item.action,
                    "zone": zone["name"],
                    "text": f"医疗处置 {healed} 人（{item.resources.get('medical_kits', 0)} 个医疗包）",
                    "value": healed,
                }
            )
        elif item.action == "dispatch_supply":
            zone = state.zones.get(item.target_area)
            if not zone:
                continue
            amount = min(zone["population"], item.resources.get("volunteers", 0) * SUPPLY_PER_VOLUNTEER)
            supported += amount
            effects.append(
                {
                    "action": item.action,
                    "zone": zone["name"],
                    "text": f"物资覆盖 {amount} 人",
                    "value": amount,
                }
            )
        elif item.action == "open_route":
            edge = state.edges.get(item.target_area)
            if edge:
                edge["open"] = True
                edge["reason"] = "已抢通"
                effects.append(
                    {"action": item.action, "zone": edge["name"], "text": "道路已抢通", "value": 1}
                )
        elif item.action == "close_road":
            edge = state.edges.get(item.target_area)
            if edge and item.modified:
                for zid in (edge["a"], edge["b"]):
                    state.zones[zid]["risk"] = max(0.0, round(state.zones[zid]["risk"] - 0.4, 1))
                effects.append(
                    {"action": item.action, "zone": edge["name"], "text": item.note or "设警戒点", "value": 0}
                )
            elif edge:
                edge["open"] = False
                edge["reason"] = "按方案封闭"
                effects.append(
                    {"action": item.action, "zone": edge["name"], "text": "道路已封闭", "value": 0}
                )
        elif item.action == "notify_students":
            effects.append(
                {"action": item.action, "zone": "全校", "text": "发布避险广播覆盖全体在校学生", "value": 1}
            )

    spent = sum(sum(item.resources.values()) for item in resolution.items)
    metrics = Metrics(
        trapped_total=trapped_total,
        rescued=rescued,
        stabilized=stabilized,
        supported=supported,
        coverage=round(min(1.0, (rescued + stabilized * 0.5) / trapped_total), 3) if trapped_total else 0.0,
        vehicles_used=buses_used,
        conflicts=len(resolution.conflicts),
        conflicts_resolved=sum(1 for c in resolution.conflicts if c.resolution),
        response_minutes=round(4.0 + 1.6 * len(resolution.items), 1),
        efficiency=round((rescued + stabilized * 0.5 + supported * 0.1) / max(1, spent), 2),
    )
    return effects, metrics


def snapshot(state: SimState) -> dict:
    """给前端展示用的状态快照。"""
    data = state.to_dict()
    data["zones"] = [
        {
            **zone,
            "medical_need": zone["medical_need"],
            "status": _zone_status(zone),
        }
        for zone in state.zones.values()
    ]
    data["trapped_total"] = state.total_trapped()
    return data


def _zone_status(zone: dict) -> str:
    if zone["trapped"] > 0:
        return "trapped"
    if zone["hazard"] >= 6:
        return "flooded"
    return "normal"


BASELINE_WINDOW_MINUTES = 30


def baseline_outcome(state: SimState) -> dict:
    """无干预推演：不做任何调度，30 分钟内风险如何演化。

    这是给对照用的粗粒度模型，不是真实伤亡预测：
      风险暴露人数 = 被困人数 × (0.15 + 0.35 × 危险度/10)
      预计伤亡人数 = 被困人数 × (0.03 + 0.12 × 危险度/10)
    若该区域既到不了医务室也到不了避难所（孤立），恶化系数再乘 1.15。
    """
    at_risk = 0.0
    casualties = 0.0
    isolated = 0
    for zone in state.zones.values():
        trapped = zone["trapped"]
        if trapped <= 0:
            continue
        h = min(1.0, zone["hazard"] / 10)
        factor = 0.15 + 0.35 * h
        if not (state.path_exists(zone["id"], "clinic") or state.path_exists(zone["id"], "gym")):
            factor *= 1.15
            isolated += 1
        at_risk += trapped * factor
        casualties += trapped * (0.03 + 0.12 * h)

    return {
        "window_minutes": BASELINE_WINDOW_MINUTES,
        "at_risk": round(at_risk),
        "casualties": round(casualties),
        "isolated_zones": isolated,
        "trapped_total": state.total_trapped(),
        "note": "无干预推演：粗粒度模型，用于对照，不是真实伤亡预测",
    }


def risk_coverage(baseline: dict, metrics: Metrics) -> float:
    """本方案覆盖了多少“无干预下会恶化的风险暴露”。"""
    at_risk = baseline.get("at_risk", 0)
    if not at_risk:
        return 0.0
    handled = metrics.rescued + metrics.stabilized + metrics.supported * 0.2
    return round(min(1.0, handled / at_risk), 3)


def evolve_state(state: SimState, severity: int, round_no: int) -> dict:
    """两轮之间环境继续恶化：危险度上升、被困人数增加、志愿者补充。

    这是多轮演示的关键——同一批角色在新态势下要重新决策，
    所以“第 2 轮”不是重复跑一遍，而是对变化后的局面再决策。
    """
    delta = round(0.6 * severity, 1)
    for zone in state.zones.values():
        if zone["trapped"] > 0:
            zone["hazard"] = round(min(10.0, zone["hazard"] + delta), 1)
            zone["trapped"] += max(1, int(zone["trapped"] * 0.05))
            zone["medical_need"] += max(1, int(zone["medical_need"] * 0.1))
        zone["risk"] = round(min(10.0, zone["hazard"] * 0.7 + (3.0 if zone["trapped"] else 0.0)), 1)
    state.resources["volunteers"] = min(60, state.resources.get("volunteers", 0) + 4)
    return {
        "round": round_no,
        "hazard_delta": delta,
        "note": f"第 {round_no} 轮前：危险度 +{delta}，被困人数 +5%，志愿者 +4",
    }


def merge_metrics(round_metrics: list[Metrics], trapped_total: int) -> Metrics:
    """把多轮指标合并成一份总账。"""
    if not round_metrics:
        return Metrics(trapped_total=trapped_total)
    rescued = sum(m.rescued for m in round_metrics)
    stabilized = sum(m.stabilized for m in round_metrics)
    supported = sum(m.supported for m in round_metrics)
    return Metrics(
        trapped_total=trapped_total,
        rescued=rescued,
        stabilized=stabilized,
        supported=supported,
        coverage=round(min(1.0, (rescued + stabilized * 0.5) / trapped_total), 3) if trapped_total else 0.0,
        vehicles_used=sum(m.vehicles_used for m in round_metrics),
        conflicts=sum(m.conflicts for m in round_metrics),
        conflicts_resolved=sum(m.conflicts_resolved for m in round_metrics),
        response_minutes=round(sum(m.response_minutes for m in round_metrics), 1),
        efficiency=round(
            sum(m.efficiency * max(1, m.trapped_total) for m in round_metrics)
            / max(1, sum(m.trapped_total for m in round_metrics)),
            2,
        ),
    )


def summarize_item(item: PlanItem, state: SimState) -> dict:
    """方案项的展示文本。"""
    target = state.zone_name(item.target_area)
    if item.action in ("close_road", "open_route"):
        target = state.edge_name(item.target_area)
    action_text = {
        "evacuate": "人员转移",
        "send_medical_team": "医疗支援",
        "dispatch_supply": "物资投送",
        "close_road": "道路管控",
        "open_route": "道路抢通",
        "notify_students": "信息发布",
        "idle": "待命",
    }[item.action]
    return {
        "agent": item.agent,
        "action": item.action,
        "action_text": action_text,
        "target": target,
        "priority": item.priority,
        "resources": item.resources,
        "reason": item.reason,
        "modified": item.modified,
        "note": item.note,
        "path": list(item.path),
    }
