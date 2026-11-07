"""虚拟校园拓扑与暴雨场景生成器。

所有数据均为自建模拟数据，不涉及任何真实校园信息（见 AGENTS.md §1）。
"""

from __future__ import annotations

import random

from app.engine.state import SimState

ZONES: list[dict] = [
    {"id": "gate", "name": "校门及道路", "kind": "corridor", "x": 450, "y": 500, "population": 60},
    {"id": "teaching", "name": "教学楼", "kind": "dense", "x": 420, "y": 350, "population": 320},
    {"id": "dorm_a", "name": "学生宿舍A", "kind": "residential", "x": 160, "y": 300, "population": 260},
    {"id": "dorm_b", "name": "学生宿舍B", "kind": "residential", "x": 230, "y": 110, "population": 240},
    {"id": "canteen", "name": "食堂", "kind": "flow", "x": 690, "y": 330, "population": 180},
    {"id": "library", "name": "图书馆", "kind": "dense", "x": 700, "y": 170, "population": 150},
    {"id": "clinic", "name": "医务室", "kind": "medical", "x": 560, "y": 420, "population": 20},
    {"id": "gym", "name": "体育馆", "kind": "shelter", "x": 790, "y": 470, "population": 30},
]

EDGES: list[dict] = [
    {"id": "e_main", "a": "gate", "b": "teaching", "name": "主干道"},
    {"id": "e_road2", "a": "gate", "b": "dorm_a", "name": "2号道路"},
    {"id": "e_east", "a": "gate", "b": "gym", "name": "东环路"},
    {"id": "e_teach_dorma", "a": "teaching", "b": "dorm_a", "name": "教学路"},
    {"id": "e_teach_canteen", "a": "teaching", "b": "canteen", "name": "食堂路"},
    {"id": "e_teach_clinic", "a": "teaching", "b": "clinic", "name": "医务路"},
    {"id": "e_north", "a": "canteen", "b": "dorm_b", "name": "北区路"},
    {"id": "e_dormline", "a": "dorm_a", "b": "dorm_b", "name": "宿舍环线"},
    {"id": "e_lib", "a": "canteen", "b": "library", "name": "图书馆路"},
    {"id": "e_south", "a": "gym", "b": "library", "name": "南环路"},
    {"id": "e_clinic_canteen", "a": "clinic", "b": "canteen", "name": "医务支路"},
]

RESOURCES: dict[str, int] = {
    "medical_kits": 14,
    "volunteers": 36,
    "buses": 2,
    "boats": 1,
    "barricades": 6,
}

LOW_LYING = {"gate", "teaching", "dorm_a"}

BUS_CAPACITY = 25  # 每辆校车单轮转移人数


def build_scenario(seed: int = 7, severity: int = 3) -> SimState:
    """生成一次暴雨突发事件。

    设计要点：severity >= 2 时，“教学路”与“宿舍环线”被淹没，
    宿舍A 只剩“2号道路”一条对外通道 —— 这为后续的封路冲突埋下伏笔。
    """
    severity = max(1, min(3, int(severity)))
    rng = random.Random(seed * 131 + severity)

    zones: dict[str, dict] = {}
    for z in ZONES:
        low = z["id"] in LOW_LYING
        span = 9.0 if low else 4.0
        water = round(rng.uniform(2.5, span) * severity / 3.0, 1)
        base_trapped = {"dorm_a": 90, "dorm_b": 45, "library": 25, "teaching": 60, "canteen": 12}
        trapped = 0
        if z["id"] in base_trapped:
            trapped = int(base_trapped[z["id"]] * (0.6 + 0.4 * severity / 3) + rng.randint(0, 8))
        zones[z["id"]] = {
            **z,
            "water": water,
            "risk": round(min(10.0, water * 0.7 + (3.0 if trapped else 0.0)), 1),
            "trapped": trapped,
            "evacuated": 0,
            "medical_need": int(trapped * 0.3),
            "supply_need": z["population"],
        }

    edges: dict[str, dict] = {}
    for e in EDGES:
        edges[e["id"]] = {**e, "open": True, "reason": ""}

    if severity >= 2:
        edges["e_teach_dorma"].update(open=False, reason="教学路积水 45cm，禁止通行")
        edges["e_dormline"].update(open=False, reason="宿舍环线中段塌陷，已封闭")
    if severity >= 3:
        edges["e_south"].update(open=False, reason="南环路积水 30cm，限行")

    title = {1: "校园暴雨（蓝色预警）", 2: "校园暴雨（黄色预警）", 3: "校园暴雨（橙色预警）"}[severity]
    event = {
        "title": title,
        "severity": severity,
        "description": "强降雨导致校园多处积水，部分道路中断，宿舍区有学生被困，需多部门协同处置。",
    }

    return SimState(
        seed=seed,
        severity=severity,
        event=event,
        zones=zones,
        edges=edges,
        resources=dict(RESOURCES),
    )
