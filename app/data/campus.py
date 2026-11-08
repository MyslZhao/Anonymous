"""虚拟校园拓扑：8 个区域、11 条道路与基础资源库存。

这里只放「与场景无关」的静态数据；具体突发事件见 app/data/scenarios.py。
所有数据均为自建模拟数据，不涉及任何真实校园信息（见 AGENTS.md §1）。
"""

from __future__ import annotations

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
