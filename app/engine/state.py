"""模拟状态：校园拓扑上的可变数据（水位、道路、被困人员、资源）。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class SimState:
    seed: int
    severity: int
    event: dict = field(default_factory=dict)
    zones: dict[str, dict] = field(default_factory=dict)
    edges: dict[str, dict] = field(default_factory=dict)
    resources: dict[str, int] = field(default_factory=dict)
    roles: dict = field(default_factory=dict)

    # ---------- 角色 ----------

    def medical_zone(self) -> str | None:
        return self.roles.get("medical") or ("clinic" if "clinic" in self.zones else None)

    def shelter_zones(self) -> list[str]:
        shelters = [z for z in (self.roles.get("shelters") or []) if z in self.zones]
        if shelters:
            return shelters
        return [z for z in ("gym", "gate") if z in self.zones]

    def depot_zone(self) -> str | None:
        return self.roles.get("depot") or ("teaching" if "teaching" in self.zones else None)

    def care_zones(self) -> list[str]:
        """能提供救治/避难的目标：医疗区 + 避难所。"""
        zones = list(self.shelter_zones())
        medical = self.medical_zone()
        if medical and medical not in zones:
            zones.append(medical)
        return [z for z in zones if z in self.zones]

    # ---------- 查询 ----------

    def trapped_zones(self) -> list[str]:
        return [zid for zid, z in self.zones.items() if z["trapped"] > 0]

    def total_trapped(self) -> int:
        return sum(z["trapped"] for z in self.zones.values())

    def zone_name(self, zid: str) -> str:
        return self.zones[zid]["name"] if zid in self.zones else zid

    def edge_name(self, eid: str) -> str:
        return self.edges[eid]["name"] if eid in self.edges else eid

    def find_edge(self, name_or_id: str) -> str | None:
        """允许 Agent 用边 id 或自然语言里的路名指代一条路。"""
        token = (name_or_id or "").strip()
        if token in self.edges:
            return token
        for eid, edge in self.edges.items():
            if edge["name"] == token:
                return eid
        for eid, edge in self.edges.items():
            if token and (token in edge["name"] or edge["name"] in token):
                return eid
        return None

    def path_exists(self, src: str, dst: str, extra_closed: set[str] | None = None) -> bool:
        """在“当前开放道路 - 额外封锁”条件下判断可达性。"""
        if src == dst:
            return True
        closed = extra_closed or set()
        adj: dict[str, list[str]] = defaultdict(list)
        for eid, edge in self.edges.items():
            if not edge["open"] or eid in closed:
                continue
            adj[edge["a"]].append(edge["b"])
            adj[edge["b"]].append(edge["a"])
        seen = {src}
        queue = [src]
        while queue:
            node = queue.pop(0)
            for nxt in adj[node]:
                if nxt == dst:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return False

    def find_path(self, src: str, dst: str, extra_closed: set[str] | None = None) -> list[str]:
        """返回从 src 到 dst 的边 id 序列（BFS 最短路）；不可达返回空列表。"""
        if src == dst:
            return []
        closed = extra_closed or set()
        adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for eid, edge in self.edges.items():
            if not edge["open"] or eid in closed:
                continue
            adj[edge["a"]].append((edge["b"], eid))
            adj[edge["b"]].append((edge["a"], eid))

        prev: dict[str, tuple[str, str]] = {}
        seen = {src}
        queue = [src]
        while queue:
            node = queue.pop(0)
            for nxt, eid in adj[node]:
                if nxt in seen:
                    continue
                prev[nxt] = (node, eid)
                if nxt == dst:
                    path: list[str] = []
                    cursor = nxt
                    while cursor != src:
                        parent, edge_id = prev[cursor]
                        path.append(edge_id)
                        cursor = parent
                    return list(reversed(path))
                seen.add(nxt)
                queue.append(nxt)
        return []

    def isolating_edges(self, extra_closed: set[str] | None = None) -> list[str]:
        """返回“一旦封锁，就会让仍有被困人员的区域失去唯一通道”的边。"""
        risky: list[str] = []
        care = self.care_zones()
        if not care:
            return risky
        for eid, edge in self.edges.items():
            if not edge["open"]:
                continue
            blocked = set(extra_closed or set()) | {eid}
            for zid in self.trapped_zones():
                # 判据：封锁前能到救治点，封锁后不能 —— 即被孤立
                before = any(self.path_exists(zid, zone) for zone in care)
                after = any(self.path_exists(zid, zone, blocked) for zone in care)
                if before and not after:
                    risky.append(eid)
                    break
        return risky

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "severity": self.severity,
            "event": self.event,
            "zones": list(self.zones.values()),
            "edges": list(self.edges.values()),
            "resources": dict(self.resources),
            "roles": dict(self.roles),
            "trapped_total": self.total_trapped(),
            "blocked_roads": [e["name"] for e in self.edges.values() if not e["open"]],
        }
