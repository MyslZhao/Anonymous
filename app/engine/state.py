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

    def isolating_edges(self, extra_closed: set[str] | None = None) -> list[str]:
        """返回“一旦封锁，就会让仍有被困人员的区域失去唯一通道”的边。"""
        risky: list[str] = []
        for eid, edge in self.edges.items():
            if not edge["open"]:
                continue
            blocked = set(extra_closed or set()) | {eid}
            for zid in self.trapped_zones():
                # 判据：封锁前能到达医务室或避难所，封锁后不能 —— 即被孤立
                before = self.path_exists(zid, "clinic") or self.path_exists(zid, "gym")
                after = self.path_exists(zid, "clinic", blocked) or self.path_exists(
                    zid, "gym", blocked
                )
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
            "trapped_total": self.total_trapped(),
            "blocked_roads": [e["name"] for e in self.edges.values() if not e["open"]],
        }
