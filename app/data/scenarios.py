"""应急场景注册表。

同一个校园拓扑（8 区域 / 11 条道路）上挂不同突发事件：
暴雨内涝、宿舍火灾、大面积停电、食堂食物中毒。

新增场景三步：写一个 `_build_xxx(seed, severity, options) -> SimState`，
注册进 `SCENARIOS`，再补一条测试。规则引擎与前端无需改动。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from app.data.campus import EDGES, RESOURCES, ZONES
from app.data.maps import MapSpec, derive_roles, map_to_state_parts
from app.engine.state import SimState

SEVERITY_TITLE = {1: "蓝色", 2: "黄色", 3: "橙色"}

MAX_SCALE = 5.0
MAX_BUSES = 20
MAX_BOATS = 10


@dataclass(frozen=True)
class ScenarioOptions:
    """演示时可调的场景参数：只影响“事件规模”和“运力”，不改变场景逻辑。"""

    population_scale: float = 1.0
    buses: int | None = None
    boats: int | None = None

    @classmethod
    def clamp(cls, population_scale: float = 1.0, buses: int | None = None, boats: int | None = None):
        scale = 1.0 if population_scale is None else float(population_scale)
        scale = max(0.1, min(MAX_SCALE, scale))
        if buses is not None:
            buses = max(0, min(MAX_BUSES, int(buses)))
        if boats is not None:
            boats = max(0, min(MAX_BOATS, int(boats)))
        return cls(population_scale=round(scale, 2), buses=buses, boats=boats)


# ---------------------------------------------------------------- 工具


def _fresh_zones() -> dict[str, dict]:
    return {
        z["id"]: {
            **z,
            "hazard": 0.0,
            "risk": 0.0,
            "trapped": 0,
            "evacuated": 0,
            "medical_need": 0,
            "supply_need": z["population"],
        }
        for z in ZONES
    }


def _fresh_edges() -> dict[str, dict]:
    return {e["id"]: {**e, "open": True, "reason": ""} for e in EDGES}


def _block(edges: dict[str, dict], edge_id: str, reason: str) -> None:
    edges[edge_id]["open"] = False
    edges[edge_id]["reason"] = reason


def _jar(rng: random.Random, base: int, spread: int) -> int:
    return max(0, base + rng.randint(-spread, spread))


def _finish(
    seed: int,
    severity: int,
    options: ScenarioOptions,
    *,
    scenario: str,
    title: str,
    description: str,
    hazard_label: str,
    trapped_label: str,
    zones: dict[str, dict],
    edges: dict[str, dict],
    resources: dict[str, int],
) -> SimState:
    scale = options.population_scale
    for zone in zones.values():
        if scale != 1.0:
            zone["population"] = max(1, round(zone["population"] * scale))
            zone["trapped"] = max(0, round(zone["trapped"] * scale))
            zone["supply_need"] = zone["population"]
        zone["risk"] = round(min(10.0, zone["hazard"] * 0.7 + (3.0 if zone["trapped"] else 0.0)), 1)
        zone["medical_need"] = int(zone["trapped"] * zone.pop("_medical_ratio", 0.3))

    if options.buses is not None:
        resources["buses"] = options.buses
    if options.boats is not None:
        resources["boats"] = options.boats

    return SimState(
        seed=seed,
        severity=severity,
        roles=derive_roles(zones),
        event={
            "id": scenario,
            "title": title,
            "description": description,
            "severity": severity,
            "hazard_label": hazard_label,
            "trapped_label": trapped_label,
            "population_scale": scale,
        },
        zones=zones,
        edges=edges,
        resources=resources,
    )


# ---------------------------------------------------------------- 场景


def _build_rainstorm(seed: int, severity: int, options: ScenarioOptions) -> SimState:
    rng = random.Random(seed * 131 + severity)
    zones = _fresh_zones()
    edges = _fresh_edges()

    low_lying = {"gate", "teaching", "dorm_a"}
    for zone in zones.values():
        span = 9.0 if zone["id"] in low_lying else 4.0
        zone["hazard"] = round(rng.uniform(2.5, span) * severity / 3.0, 1)

    for zid, base in {"dorm_a": 90, "dorm_b": 45, "library": 25, "teaching": 60, "canteen": 12}.items():
        zones[zid]["trapped"] = _jar(rng, int(base * (0.6 + 0.4 * severity / 3)), 8)
        zones[zid]["_medical_ratio"] = 0.3

    if severity >= 2:
        _block(edges, "e_teach_dorma", "教学路积水 45cm，禁止通行")
        _block(edges, "e_dormline", "宿舍环线中段塌陷，已封闭")
    if severity >= 3:
        _block(edges, "e_south", "南环路积水 30cm，限行")

    return _finish(
        seed,
        severity,
        options,
        scenario="rainstorm",
        title=f"校园暴雨内涝（{SEVERITY_TITLE[severity]}预警）",
        description="强降雨导致校园多处积水，部分道路中断，宿舍区有学生被困，需多部门协同处置。",
        hazard_label="积水",
        trapped_label="被困",
        zones=zones,
        edges=edges,
        resources=dict(RESOURCES),
    )


def _build_fire(seed: int, severity: int, options: ScenarioOptions) -> SimState:
    rng = random.Random(seed * 173 + severity)
    zones = _fresh_zones()
    edges = _fresh_edges()

    zones["dorm_a"]["hazard"] = round(rng.uniform(7.5, 9.8), 1)
    zones["dorm_b"]["hazard"] = round(rng.uniform(2.0, 4.5), 1)
    zones["teaching"]["hazard"] = round(rng.uniform(0.5, 2.5), 1)
    zones["canteen"]["hazard"] = round(rng.uniform(0.2, 1.2), 1)

    zones["dorm_a"]["trapped"] = _jar(rng, 60 + 30 * severity, 12)
    zones["dorm_b"]["trapped"] = _jar(rng, 15 + 8 * severity, 6)
    zones["teaching"]["trapped"] = _jar(rng, 8 + 5 * severity, 4)
    for zid in ("dorm_a", "dorm_b", "teaching"):
        zones[zid]["_medical_ratio"] = 0.5

    if severity >= 2:
        _block(edges, "e_teach_dorma", "消防车作业占用，教学路临时封闭")
        _block(edges, "e_dormline", "火势沿连廊蔓延，宿舍环线封闭")

    return _finish(
        seed,
        severity,
        options,
        scenario="fire",
        title=f"学生宿舍A 火灾（{SEVERITY_TITLE[severity]}响应）",
        description="宿舍A 中层起火并向上蔓延，浓烟涌入楼道，部分学生被困房间，宿舍环线与教学路已封闭。",
        hazard_label="火势",
        trapped_label="被困",
        zones=zones,
        edges=edges,
        resources={"medical_kits": 10, "volunteers": 30, "buses": 2, "boats": 0, "barricades": 6},
    )


def _build_blackout(seed: int, severity: int, options: ScenarioOptions) -> SimState:
    rng = random.Random(seed * 197 + severity)
    zones = _fresh_zones()
    edges = _fresh_edges()

    for zid, span in (("dorm_a", (6.0, 9.0)), ("library", (5.0, 9.0)), ("teaching", (3.5, 7.0))):
        zones[zid]["hazard"] = round(rng.uniform(*span), 1)

    zones["dorm_a"]["trapped"] = _jar(rng, 20 + 14 * severity, 6)
    zones["library"]["trapped"] = _jar(rng, 15 + 12 * severity, 6)
    zones["teaching"]["trapped"] = _jar(rng, 5 + 5 * severity, 3)
    for zid in ("dorm_a", "library", "teaching"):
        zones[zid]["_medical_ratio"] = 0.2

    if severity >= 2:
        _block(edges, "e_teach_dorma", "电缆沟检修，教学路临时封闭")
        _block(edges, "e_dormline", "电梯救援设备停放占用，宿舍环线封闭")

    return _finish(
        seed,
        severity,
        options,
        scenario="blackout",
        title=f"校园大面积停电（{SEVERITY_TITLE[severity]}响应）",
        description="供电主干线故障，宿舍A、图书馆与教学楼失电，电梯困人、楼道照明失效，需组织滞留人员疏散。",
        hazard_label="影响",
        trapped_label="滞留",
        zones=zones,
        edges=edges,
        resources={"medical_kits": 8, "volunteers": 40, "buses": 2, "boats": 0, "barricades": 4},
    )


def _build_food_safety(seed: int, severity: int, options: ScenarioOptions) -> SimState:
    rng = random.Random(seed * 211 + severity)
    zones = _fresh_zones()
    edges = _fresh_edges()

    zones["canteen"]["hazard"] = round(rng.uniform(7.0, 9.5), 1)
    zones["dorm_a"]["hazard"] = round(rng.uniform(4.0, 7.0), 1)
    zones["dorm_b"]["hazard"] = round(rng.uniform(3.0, 6.0), 1)
    zones["library"]["hazard"] = round(rng.uniform(1.0, 3.0), 1)

    zones["canteen"]["trapped"] = _jar(rng, 35 + 15 * severity, 8)
    zones["dorm_a"]["trapped"] = _jar(rng, 25 + 10 * severity, 8)
    zones["dorm_b"]["trapped"] = _jar(rng, 15 + 8 * severity, 6)
    zones["library"]["trapped"] = _jar(rng, 5 + 4 * severity, 3)
    for zid in ("canteen", "dorm_a", "dorm_b", "library"):
        zones[zid]["_medical_ratio"] = 0.8

    if severity >= 2:
        _block(edges, "e_north", "北区路设流调卡口，仅救援车辆通行")
        _block(edges, "e_teach_dorma", "消杀作业，教学路临时封闭")
        _block(edges, "e_dormline", "宿舍环线设隔离带，禁止通行")

    return _finish(
        seed,
        severity,
        options,
        scenario="food_safety",
        title=f"食堂疑似食物中毒（{SEVERITY_TITLE[severity]}响应）",
        description="多名学生在食堂就餐后出现腹痛呕吐，病例向宿舍扩散，医疗物资紧张，需转运与流调同步进行。",
        hazard_label="病例密度",
        trapped_label="待救治",
        zones=zones,
        edges=edges,
        resources={"medical_kits": 6, "volunteers": 30, "buses": 2, "boats": 0, "barricades": 4},
    )


# ---------------------------------------------------------------- 注册


SCENARIOS: dict[str, dict] = {
    "rainstorm": {
        "name": "暴雨内涝",
        "summary": "积水断路、人员被困，路线冲突最典型",
        "build": _build_rainstorm,
    },
    "fire": {
        "name": "宿舍火灾",
        "summary": "火势蔓延、消防通道占用，医疗需求高",
        "build": _build_fire,
    },
    "blackout": {
        "name": "大面积停电",
        "summary": "电梯困人、照明失效，资源相对充裕",
        "build": _build_blackout,
    },
    "food_safety": {
        "name": "食物中毒",
        "summary": "病例扩散、医疗包极度紧张，流调封控",
        "build": _build_food_safety,
    },
}

DEFAULT_SCENARIO = "rainstorm"

# 在自定义地图上生成事件时使用的场景画像（与具体区域 id 无关）
SCENARIO_PROFILES: dict[str, dict] = {
    "rainstorm": {
        "event_name": "校园暴雨内涝",
        "suffix": "预警",
        "description": "强降雨导致校园多处积水，部分道路中断，宿舍区有学生被困，需多部门协同处置。",
        "hazard_label": "积水",
        "trapped_label": "被困",
        "trapped_base": 90,
        "medical_ratio": 0.3,
        "hazard_span": (5.5, 9.5),
        "resources": dict(RESOURCES),
    },
    "fire": {
        "event_name": "校园火灾",
        "suffix": "响应",
        "description": "建筑起火并向上蔓延，浓烟涌入楼道，部分人员被困房间，多地道路被占用。",
        "hazard_label": "火势",
        "trapped_label": "被困",
        "trapped_base": 100,
        "medical_ratio": 0.5,
        "hazard_span": (7.0, 9.8),
        "resources": {"medical_kits": 10, "volunteers": 30, "buses": 2, "boats": 0, "barricades": 6},
    },
    "blackout": {
        "event_name": "校园大面积停电",
        "suffix": "响应",
        "description": "供电主干线故障，多处失电，电梯困人、楼道照明失效，需组织滞留人员疏散。",
        "hazard_label": "影响",
        "trapped_label": "滞留",
        "trapped_base": 55,
        "medical_ratio": 0.2,
        "hazard_span": (4.0, 8.0),
        "resources": {"medical_kits": 8, "volunteers": 40, "buses": 2, "boats": 0, "barricades": 4},
    },
    "food_safety": {
        "event_name": "疑似食物中毒",
        "suffix": "响应",
        "description": "多人出现腹痛呕吐，病例向住宿区扩散，医疗物资紧张，需转运与流调同步进行。",
        "hazard_label": "病例密度",
        "trapped_label": "待救治",
        "trapped_base": 80,
        "medical_ratio": 0.8,
        "hazard_span": (5.0, 9.0),
        "resources": {"medical_kits": 6, "volunteers": 30, "buses": 2, "boats": 0, "barricades": 4},
    },
}

KIND_PRIORITY = {"residential": 0, "dense": 1, "flow": 2, "corridor": 3, "medical": 4, "shelter": 5}
# 只有这三类区域会产生被困人员；医疗点、避难所、校门不应"被困"
TRAPPABLE_KINDS = {"residential", "dense", "flow"}


def _close_routes_for_demo(zones: dict[str, dict], edges: dict[str, dict], trapped_ids: list[str]) -> None:
    """在自定义地图上人为制造“唯一通道”：保留每个受困区的至少一条救援通道，其余尽量封闭。

    只在不会彻底切断救援、也不会连带切断其他受困区的前提下封闭，
    因此自定义地图同样能演示“封路会切断唯一通道”的冲突。
    """
    probe = SimState(seed=0, severity=1, zones=zones, edges=edges, roles=derive_roles(zones))
    care = probe.care_zones()
    if not care:
        return
    for zid in trapped_ids:
        incident = sorted(
            (e for e in edges.values() if zid in (e["a"], e["b"]) and e["open"]), key=lambda e: e["id"]
        )
        for edge in incident:
            closed = {eid for eid, item in edges.items() if not item["open"]} | {edge["id"]}
            if not all(probe.path_exists(zid, target, closed) for target in care):
                continue  # 封闭会让受困区到不了某个救治点，跳过
            broken = any(
                not all(probe.path_exists(other, target, closed) for target in care)
                for other in trapped_ids
                if other != zid
            )
            if broken:
                continue
            allowed = sum(1 for e in edges.values() if zid in (e["a"], e["b"]) and e["open"])
            if allowed <= 1:
                break
            edges[edge["id"]]["open"] = False
            edges[edge["id"]]["reason"] = f"{edge['name']} 因突发事件封闭"


def _build_on_map(
    map_spec: MapSpec,
    scenario: str,
    seed: int,
    severity: int,
    options: ScenarioOptions,
) -> SimState:
    """在外部传入的地图上生成一次事件（按 kind 语义选取受困区与危险度）。"""
    profile = SCENARIO_PROFILES[scenario]
    rng = random.Random(seed * 991 + severity)
    zones, edges, resources = map_to_state_parts(map_spec)

    candidates = sorted(
        (z for z in zones.values() if z["kind"] in TRAPPABLE_KINDS),
        key=lambda z: KIND_PRIORITY.get(z["kind"], 9),
    )
    trapped_targets = candidates[: min(3, len(candidates))]
    for index, zone in enumerate(trapped_targets):
        base = profile["trapped_base"] * (1 - 0.25 * index)
        zone["trapped"] = max(1, int(base * (0.6 + 0.4 * severity / 3)))
        zone["hazard"] = round(min(10.0, rng.uniform(*profile["hazard_span"]) * severity / 3), 1)
        zone["_medical_ratio"] = profile["medical_ratio"]
    for zone in zones.values():
        if zone not in trapped_targets:
            zone["hazard"] = round(rng.uniform(0.2, 2.5) * severity / 3, 1)

    if severity >= 2:
        _close_routes_for_demo(zones, edges, [z["id"] for z in trapped_targets])

    title = f"{profile['event_name']}（{SEVERITY_TITLE[severity]}{profile['suffix']}）"
    return _finish(
        seed,
        severity,
        options,
        scenario=scenario,
        title=title,
        description=profile["description"],
        hazard_label=profile["hazard_label"],
        trapped_label=profile["trapped_label"],
        zones=zones,
        edges=edges,
        resources=dict(profile["resources"]),
    )


def list_scenarios() -> list[dict]:
    return [
        {"id": sid, "name": spec["name"], "summary": spec["summary"]}
        for sid, spec in SCENARIOS.items()
    ]


def build_scenario(
    seed: int = 7,
    severity: int = 3,
    scenario: str = DEFAULT_SCENARIO,
    options: ScenarioOptions | None = None,
    map_spec: MapSpec | None = None,
) -> SimState:
    """按场景 id 生成一次突发事件。未知 id 抛 ValueError。

    options 用于演示时的即时调整：人数规模倍数、校车数、冲锋舟数。
    map_spec 非空时在外部地图上生成事件；为空则使用内置演示地图。
    """
    spec = SCENARIOS.get(scenario)
    if spec is None:
        raise ValueError(f"未知场景：{scenario}")
    severity = max(1, min(3, int(severity)))
    if map_spec is not None:
        return _build_on_map(map_spec, scenario, seed, severity, options or ScenarioOptions())
    builder: Callable[[int, int, ScenarioOptions], SimState] = spec["build"]
    return builder(seed, severity, options or ScenarioOptions())
