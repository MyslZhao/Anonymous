"""六角色元信息（前端配色与展示名）。"""

from __future__ import annotations

AGENT_META: dict[str, dict] = {
    "command": {"name": "应急指挥", "color": "#a78bfa", "icon": "◎", "desc": "汇总信息、识别冲突、提出整体方案"},
    "medical": {"name": "医疗", "color": "#fb7185", "icon": "✚", "desc": "伤员评估、医疗包与救护力量分配"},
    "security": {"name": "保卫", "color": "#fbbf24", "icon": "◈", "desc": "道路封锁、人员疏散、安全区判断"},
    "logistics": {"name": "后勤", "color": "#34d399", "icon": "▣", "desc": "饮用水、食品、雨具与救援物资调配"},
    "traffic": {"name": "交通", "color": "#60a5fa", "icon": "↔", "desc": "通行状况、车辆路线与救援路径规划"},
    "student": {"name": "学生事务", "color": "#f472b6", "icon": "☺", "desc": "学生反馈、人员需求与志愿者组织"},
}


# 每个角色允许提出的行动。越权提议（例如“学生事务”申请医疗队）会被直接打回，
# 因为演示时评委一眼就能看出这不像该部门的职责。
ROLE_ACTIONS: dict[str, set[str]] = {
    "command": {"notify_students", "open_route", "dispatch_supply", "idle"},
    "medical": {"send_medical_team", "notify_students", "idle"},
    "security": {"close_road", "evacuate", "notify_students", "idle"},
    "logistics": {"dispatch_supply", "notify_students", "idle"},
    "traffic": {"evacuate", "open_route", "notify_students", "idle"},
    "student": {"evacuate", "notify_students", "idle"},
}

ACTION_LABELS = {
    "send_medical_team": "医疗支援",
    "close_road": "道路管控",
    "evacuate": "人员转移",
    "dispatch_supply": "物资投送",
    "open_route": "道路抢通",
    "notify_students": "信息发布",
    "idle": "待命",
}


def role_issue(role: str, action: str) -> str | None:
    allowed = ROLE_ACTIONS.get(role)
    if allowed is None or action in allowed:
        return None
    labels = "、".join(ACTION_LABELS.get(a, a) for a in sorted(allowed))
    return f"你的角色不允许提出 {ACTION_LABELS.get(action, action)}，只能从这些行动中选：{labels}"
