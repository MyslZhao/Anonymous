"""离线 Mock Agent：无 API Key 时用确定性规则生成六角色建议。

它不是“假 AI 的兜底 UI 数据”，而是与真实模型返回同一份 JSON 契约，
因此规则引擎、前端逐步揭示、测试都能在完全离线的情况下跑通。

措辞按场景（暴雨/火灾/停电/食物中毒）切换，但动作结构与冲突设计保持一致：
校车争夺、封锁唯一通道这两个冲突点在任何场景下都会出现，方便演示与回归。
"""

from __future__ import annotations

from app.engine.state import SimState
from app.models import Proposal, ResourceRequest

SECURITY_REASON: dict[str, str] = {
    "rainstorm": "2号道路积水没过膝，存在车辆熄火与人员落水风险，申请封闭",
    "fire": "2号道路紧邻火场，浓烟沿路面外溢，申请封闭防止学生靠近",
    "blackout": "2号道路照明失效，车辆与行人混行风险高，申请临时封闭",
    "food_safety": "2号道路为疑似病例集中区主干道，申请封闭配合流调",
}

LOGISTICS_SUPPLY: dict[str, str] = {
    "rainstorm": "需补充饮用水、食品与雨具",
    "fire": "需补充毛毯、饮用水与烧伤急救物资",
    "blackout": "需补充应急照明、饮用水与充电宝",
    "food_safety": "需补充口服补液盐、消毒物资与一次性用品",
}

STUDENT_SUFFIX: dict[str, str] = {
    "rainstorm": "有学生被困在低楼层，请求校车支援",
    "fire": "学生被浓烟困在宿舍楼上，请求车辆接应",
    "blackout": "学生滞留楼道与电梯间，请求车辆转运",
    "food_safety": "多名学生出现症状，请求车辆转运就医",
}


def _ranked_trapped(state: SimState) -> list[dict]:
    return sorted(
        (z for z in state.zones.values() if z["trapped"] > 0),
        key=lambda z: z["trapped"],
        reverse=True,
    )


def mock_proposal(role: str, state: SimState) -> Proposal:
    ranked = _ranked_trapped(state)
    top = ranked[0] if ranked else state.zones["teaching"]
    second = ranked[1] if len(ranked) > 1 else top
    sid = state.event.get("id", "rainstorm")
    trapped_label = state.event.get("trapped_label", "被困")

    if role == "command":
        return Proposal(
            agent="command",
            priority="medium",
            target_area="gate",
            action="notify_students",
            reason=f"已启动{state.event.get('title', '突发事件')}响应，需全校广播避险",
        )

    if role == "medical":
        kits = min(6, state.resources["medical_kits"])
        return Proposal(
            agent="medical",
            priority="high",
            target_area=top["id"],
            action="send_medical_team",
            resource_required=ResourceRequest(medical_kits=kits, volunteers=4),
            reason=f"{top['name']}{trapped_label} {top['trapped']} 人，医疗需求 {top['medical_need']}，优先处置",
        )

    if role == "security":
        return Proposal(
            agent="security",
            priority="high",
            target_area="2号道路",
            action="close_road",
            resource_required=ResourceRequest(barricades=3),
            reason=SECURITY_REASON.get(sid, "2号道路存在通行风险，申请临时封闭"),
        )

    if role == "logistics":
        return Proposal(
            agent="logistics",
            priority="high",
            target_area=second["id"],
            action="dispatch_supply",
            resource_required=ResourceRequest(volunteers=8),
            reason=f"{second['name']}在区 {second['population']} 人，{LOGISTICS_SUPPLY.get(sid, '需补充应急物资')}",
        )

    if role == "traffic":
        buses = state.resources["buses"]
        return Proposal(
            agent="traffic",
            priority="high",
            target_area=top["id"],
            action="evacuate",
            resource_required=ResourceRequest(buses=buses),
            reason=f"{top['name']}{trapped_label} {top['trapped']} 人，建议全部 {buses} 辆校车优先转移",
        )

    if role == "student":
        return Proposal(
            agent="student",
            priority="high",
            target_area=second["id"],
            action="evacuate",
            resource_required=ResourceRequest(buses=state.resources["buses"]),
            reason=f"{second['name']}{second['trapped']} 人，{STUDENT_SUFFIX.get(sid, '请求校车支援')}",
        )

    return Proposal(agent="command", action="idle", target_area="", reason="无可用行动")
