"""领域模型：Agent 提议、裁决结果、模拟指标。

约束：Agent 只能输出结构化建议，规则引擎负责判定可行性。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AgentName = Literal["command", "medical", "security", "logistics", "traffic", "student"]

ActionType = Literal[
    "send_medical_team",
    "close_road",
    "evacuate",
    "dispatch_supply",
    "open_route",
    "notify_students",
    "idle",
]

Priority = Literal["low", "medium", "high"]

ALL_AGENTS: tuple[AgentName, ...] = (
    "command",
    "medical",
    "security",
    "logistics",
    "traffic",
    "student",
)


class ResourceRequest(BaseModel):
    """一次行动申请的资源。未申请的为 0。"""

    model_config = ConfigDict(extra="ignore")

    medical_kits: int = Field(default=0, ge=0, le=100)
    volunteers: int = Field(default=0, ge=0, le=200)
    buses: int = Field(default=0, ge=0, le=20)
    boats: int = Field(default=0, ge=0, le=20)
    barricades: int = Field(default=0, ge=0, le=50)

    def as_dict(self) -> dict[str, int]:
        return self.model_dump()

    def total(self) -> int:
        return sum(self.model_dump().values())


class Proposal(BaseModel):
    """Agent 输出的结构化建议（契约见 README §4.2）。"""

    model_config = ConfigDict(extra="ignore")

    agent: AgentName
    priority: Priority = "medium"
    target_area: str = ""
    action: ActionType = "idle"
    resource_required: ResourceRequest = Field(default_factory=ResourceRequest)
    reason: str = ""


class PlanItem(BaseModel):
    """通过裁决、可以执行的行动。"""

    agent: AgentName
    action: ActionType
    target_area: str
    priority: Priority
    resources: dict[str, int] = Field(default_factory=dict)
    reason: str = ""
    modified: bool = False
    note: str = ""


class Conflict(BaseModel):
    """Agent 之间的冲突及其裁决结论。"""

    kind: Literal["resource", "route", "duplicate", "invalid"]
    parties: list[str] = Field(default_factory=list)
    detail: str
    resolution: str


class Rejection(BaseModel):
    agent: AgentName
    action: ActionType
    target_area: str
    reason: str


class Resolution(BaseModel):
    """规则引擎一次裁决的完整产物。"""

    items: list[PlanItem] = Field(default_factory=list)
    rejected: list[Rejection] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    ledger: dict[str, int] = Field(default_factory=dict)


class Metrics(BaseModel):
    trapped_total: int = 0
    rescued: int = 0
    stabilized: int = 0
    supported: int = 0
    coverage: float = 0.0
    vehicles_used: int = 0
    conflicts: int = 0
    conflicts_resolved: int = 0
    response_minutes: float = 0.0
    efficiency: float = 0.0
