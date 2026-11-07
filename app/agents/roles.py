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


def agent_name(agent: str) -> str:
    return AGENT_META.get(agent, {}).get("name", agent)
