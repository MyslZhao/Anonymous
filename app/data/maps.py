"""自定义地图：外部传入校园拓扑（区域 + 道路），替代内置演示地图。

设计原则：
- 只要满足最小语义（至少一个医疗区、一个避难所、一个可受困区域），就能跑任何场景；
- 校验失败返回结构化错误，而不是让后续模拟莫名失败；
- 内置演示地图不受影响，不传 map_id 时行为与之前完全一致。
"""

from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, Field

from app.data.campus import RESOURCES

ZONE_KINDS = {"residential", "dense", "flow", "medical", "shelter", "corridor"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MAX_ZONES = 40
MAX_EDGES = 80
CANVAS_WIDTH = 900
CANVAS_HEIGHT = 570


class ZoneSpec(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=40)
    kind: str
    x: int = Field(ge=0, le=CANVAS_WIDTH)
    y: int = Field(ge=0, le=CANVAS_HEIGHT)
    population: int = Field(default=0, ge=0, le=50000)


class EdgeSpec(BaseModel):
    id: str
    a: str
    b: str
    name: str = Field(min_length=1, max_length=40)
    critical: bool = False


class MapSpec(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    zones: list[ZoneSpec]
    edges: list[EdgeSpec]
    resources: dict[str, int] = Field(default_factory=dict)


class MapValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("；".join(errors))
        self.errors = errors


def validate_map(spec: MapSpec) -> None:
    """校验拓扑是否满足模拟所需的最小语义。"""
    errors: list[str] = []
    if len(spec.zones) < 3:
        errors.append("至少需要 3 个区域")
    if len(spec.zones) > MAX_ZONES:
        errors.append(f"区域数量不能超过 {MAX_ZONES}")
    if len(spec.edges) > MAX_EDGES:
        errors.append(f"道路数量不能超过 {MAX_EDGES}")

    zone_ids: set[str] = set()
    for zone in spec.zones:
        if not ID_PATTERN.match(zone.id):
            errors.append(f"区域 id 非法：{zone.id!r}（只允许字母数字下划线短横，1–32 位）")
        if zone.id in zone_ids:
            errors.append(f"区域 id 重复：{zone.id}")
        zone_ids.add(zone.id)
        if zone.kind not in ZONE_KINDS:
            errors.append(f"区域 {zone.id} 的 kind 非法：{zone.kind!r}，可选 {sorted(ZONE_KINDS)}")

    edge_ids: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for edge in spec.edges:
        if not ID_PATTERN.match(edge.id):
            errors.append(f"道路 id 非法：{edge.id!r}")
        if edge.id in edge_ids:
            errors.append(f"道路 id 重复：{edge.id}")
        edge_ids.add(edge.id)
        if edge.a not in zone_ids:
            errors.append(f"道路 {edge.id} 的起点 {edge.a!r} 不存在")
        if edge.b not in zone_ids:
            errors.append(f"道路 {edge.id} 的终点 {edge.b!r} 不存在")
        if edge.a == edge.b:
            errors.append(f"道路 {edge.id} 不能连接同一个区域")
        pair = tuple(sorted((edge.a, edge.b)))
        if pair in pairs:
            errors.append(f"区域 {pair[0]} 与 {pair[1]} 之间存在重复道路")
        pairs.add(pair)

    kinds = {zone.kind for zone in spec.zones}
    if "medical" not in kinds:
        errors.append("至少需要一个 kind=medical 的区域（医疗队出发点）")
    if "shelter" not in kinds:
        errors.append("至少需要一个 kind=shelter 的区域（疏散避难所）")
    if not ({"residential", "dense", "flow"} & kinds):
        errors.append("至少需要一个可能有人被困的区域（kind 为 residential / dense / flow）")

    # 名称唯一：名称会进入提示词、地图标注和"封闭某某路"的匹配
    name_seen: dict[str, str] = {}
    for zone in spec.zones:
        if zone.name in name_seen:
            errors.append(f"区域名称重复：{zone.name}（{name_seen[zone.name]} / {zone.id}）")
        name_seen.setdefault(zone.name, zone.id)
    road_seen: dict[str, str] = {}
    for edge in spec.edges:
        if edge.name in road_seen:
            errors.append(f"道路名称重复：{edge.name}（{road_seen[edge.name]} / {edge.id}）")
        road_seen.setdefault(edge.name, edge.id)

    # 连通性：任何区域都必须能连到主图，否则场景生成后整轮行动都会被判"路径阻断"
    adjacency: dict[str, set[str]] = {zid: set() for zid in zone_ids}
    for edge in spec.edges:
        if edge.a in adjacency and edge.b in adjacency and edge.a != edge.b:
            adjacency[edge.a].add(edge.b)
            adjacency[edge.b].add(edge.a)
    if zone_ids:
        start = next(iter(zone_ids))
        visited = {start}
        stack = [start]
        while stack:
            node = stack.pop()
            for nxt in adjacency[node]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        unreachable = sorted(zone_ids - visited)
        if unreachable:
            errors.append(f"地图不连通，以下区域无法到达其他区域：{unreachable}")

    for key in spec.resources:
        if key not in RESOURCES:
            errors.append(f"未知资源项：{key}，可选 {sorted(RESOURCES)}")
        elif spec.resources[key] < 0:
            errors.append(f"资源 {key} 不能为负数")

    if errors:
        raise MapValidationError(errors)


def derive_roles(zones: dict[str, dict]) -> dict:
    """按 kind 推导规则引擎需要的角色：医疗、避难所、物资集散点。"""
    medical = next((zid for zid, z in zones.items() if z["kind"] == "medical"), None)
    shelters = [zid for zid, z in zones.items() if z["kind"] == "shelter"]
    depots = [zid for zid, z in zones.items() if z["kind"] == "dense"] or [
        zid for zid, z in zones.items() if z["kind"] == "flow"
    ]
    if not shelters:
        shelters = [zid for zid, z in zones.items() if z["kind"] == "corridor"]
    return {
        "medical": medical,
        "shelters": shelters,
        "depot": depots[0] if depots else medical,
    }


def map_to_state_parts(spec: MapSpec) -> tuple[dict[str, dict], dict[str, dict], dict[str, int]]:
    """把外部地图转换成模拟引擎内部结构。"""
    zones: dict[str, dict] = {}
    for zone in spec.zones:
        zones[zone.id] = {
            "id": zone.id,
            "name": zone.name,
            "kind": zone.kind,
            "x": zone.x,
            "y": zone.y,
            "population": zone.population,
            "hazard": 0.0,
            "risk": 0.0,
            "trapped": 0,
            "evacuated": 0,
            "medical_need": 0,
            "supply_need": zone.population,
        }
    edges: dict[str, dict] = {}
    for edge in spec.edges:
        edges[edge.id] = {
            "id": edge.id,
            "a": edge.a,
            "b": edge.b,
            "name": edge.name,
            "open": True,
            "reason": "",
            "critical": edge.critical,
        }
    resources = dict(RESOURCES)
    resources.update(spec.resources or {})
    return zones, edges, resources


def new_map_id() -> str:
    return f"map-{uuid.uuid4().hex[:8]}"
