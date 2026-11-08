"""自定义地图：校验、角色推导、场景生成与端到端。"""

import asyncio

import pytest
from pydantic import ValidationError

from app.agents.coordinator import run_simulation
from app.agents.decision import decide
from app.agents.mock import mock_proposal
from app.data.maps import MapSpec, MapValidationError, derive_roles, map_to_state_parts, validate_map
from app.data.scenarios import ScenarioOptions, build_scenario
from app.engine.rules import resolve
from app.engine.simulator import apply_plan
from app.models import ALL_AGENTS, Proposal, ResourceRequest

MAP = {
    "name": "北校区",
    "zones": [
        {"id": "d1", "name": "一号宿舍", "kind": "residential", "x": 120, "y": 120, "population": 300},
        {"id": "t1", "name": "教学楼", "kind": "dense", "x": 320, "y": 160, "population": 260},
        {"id": "c1", "name": "医务点", "kind": "medical", "x": 420, "y": 300, "population": 10},
        {"id": "s1", "name": "体育馆", "kind": "shelter", "x": 520, "y": 420, "population": 20},
        {"id": "g1", "name": "校门", "kind": "corridor", "x": 120, "y": 420, "population": 40},
    ],
    "edges": [
        {"id": "r1", "a": "d1", "b": "t1", "name": "主路"},
        {"id": "r2", "a": "t1", "b": "c1", "name": "医务路"},
        {"id": "r3", "a": "t1", "b": "s1", "name": "避难通道"},
        {"id": "r4", "a": "d1", "b": "g1", "name": "环路"},
        {"id": "r5", "a": "g1", "b": "s1", "name": "南门路"},
    ],
    "resources": {"buses": 3},
}


def _spec(**overrides) -> MapSpec:
    payload = {**MAP, **overrides}
    return MapSpec.model_validate(payload)


def test_valid_map_passes():
    validate_map(_spec())


def test_duplicate_zone_id_rejected():
    bad = dict(MAP)
    bad["zones"] = MAP["zones"] + [MAP["zones"][0]]
    with pytest.raises(MapValidationError) as exc:
        validate_map(MapSpec.model_validate(bad))
    assert any("重复" in e for e in exc.value.errors)


def test_missing_medical_rejected():
    zones = [z for z in MAP["zones"] if z["kind"] != "medical"]
    with pytest.raises(MapValidationError) as exc:
        validate_map(MapSpec.model_validate({**MAP, "zones": zones, "edges": [e for e in MAP["edges"] if "c1" not in (e["a"], e["b"])]}))
    assert any("medical" in e for e in exc.value.errors)


def test_missing_shelter_rejected():
    zones = [z for z in MAP["zones"] if z["kind"] != "shelter"]
    edges = [e for e in MAP["edges"] if "s1" not in (e["a"], e["b"])]
    with pytest.raises(MapValidationError) as exc:
        validate_map(MapSpec.model_validate({**MAP, "zones": zones, "edges": edges}))
    assert any("shelter" in e for e in exc.value.errors)


def test_edge_referencing_unknown_zone_rejected():
    edges = MAP["edges"][:-1] + [{"id": "rx", "a": "d1", "b": "ghost", "name": "幻路"}]
    with pytest.raises(MapValidationError) as exc:
        validate_map(MapSpec.model_validate({**MAP, "edges": edges}))
    assert any("ghost" in e for e in exc.value.errors)


def test_duplicate_edge_pair_rejected():
    edges = MAP["edges"] + [{"id": "r9", "a": "t1", "b": "d1", "name": "重复路"}]
    with pytest.raises(MapValidationError) as exc:
        validate_map(MapSpec.model_validate({**MAP, "edges": edges}))
    assert any("重复道路" in e for e in exc.value.errors)


def test_unknown_resource_rejected():
    with pytest.raises(MapValidationError) as exc:
        validate_map(_spec(resources={"helicopters": 2}))
    assert any("helicopters" in e for e in exc.value.errors)


def test_roles_are_derived_from_kinds():
    zones, edges, resources = map_to_state_parts(_spec())
    roles = derive_roles(zones)
    assert roles["medical"] == "c1"
    assert roles["shelters"] == ["s1"]
    assert roles["depot"] == "t1"
    assert resources["buses"] == 3
    assert resources["medical_kits"] > 0


def test_scenario_runs_on_custom_map():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm", map_spec=_spec())
    assert set(state.zones) == {"d1", "t1", "c1", "s1", "g1"}
    assert state.total_trapped() > 0
    assert state.medical_zone() == "c1"
    assert state.shelter_zones() == ["s1"]
    # 受困区必须能到达每一个救治点，否则医疗行动会被误判为路径阻断
    for zid in state.trapped_zones():
        for care in state.care_zones():
            assert state.path_exists(zid, care), f"{zid} 无法到达 {care}"


def test_custom_map_produces_executable_plan():
    state = build_scenario(seed=7, severity=3, scenario="fire", map_spec=_spec())
    resolution = resolve(state, [mock_proposal(role, state) for role in ALL_AGENTS])
    assert resolution.items, "自定义地图上至少要有一条行动可通过裁决"


def test_custom_map_end_to_end_reports_map_id():
    payload = asyncio.run(
        run_simulation(seed=7, severity=3, scenario="rainstorm", map_spec=_spec(), map_id="map-test")
    )
    assert payload["result"]["map_id"] == "map-test"
    assert payload["scenario"]["map_id"] == "map-test"
    assert {z["id"] for z in payload["scenario"]["zones"]} == {"d1", "t1", "c1", "s1", "g1"}
    assert payload["result"]["metrics"]["trapped_total"] > 0


# ---- 新补充：连通性 / 命名唯一 / 坐标范围 / 受困类型 ----


def test_disconnected_map_rejected():
    edges = [edge for edge in MAP["edges"] if edge["id"] in {"r1", "r2", "r3"}]
    with pytest.raises(MapValidationError) as exc:
        validate_map(MapSpec.model_validate({**MAP, "edges": edges}))
    assert any("不连通" in error for error in exc.value.errors)


def test_duplicate_zone_name_rejected():
    zones = MAP["zones"] + [
        {"id": "z9", "name": "教学楼", "kind": "corridor", "x": 100, "y": 500, "population": 10}
    ]
    edges = MAP["edges"] + [{"id": "r9", "a": "z9", "b": "g1", "name": "九号路"}]
    with pytest.raises(MapValidationError) as exc:
        validate_map(MapSpec.model_validate({**MAP, "zones": zones, "edges": edges}))
    assert any("区域名称重复" in error for error in exc.value.errors)


def test_duplicate_road_name_rejected():
    edges = MAP["edges"] + [{"id": "r9", "a": "g1", "b": "c1", "name": "主路"}]
    with pytest.raises(MapValidationError) as exc:
        validate_map(MapSpec.model_validate({**MAP, "edges": edges}))
    assert any("道路名称重复" in error for error in exc.value.errors)


def test_coordinate_outside_canvas_rejected():
    zones = [dict(MAP["zones"][0], x=5000)] + MAP["zones"][1:]
    with pytest.raises(ValidationError):
        MapSpec.model_validate({**MAP, "zones": zones})


def test_only_trappable_kinds_get_trapped():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm", map_spec=_spec())
    for zone in state.zones.values():
        if zone["kind"] in {"medical", "shelter", "corridor"}:
            assert zone["trapped"] == 0, f"{zone['kind']} 区域不应有被困人员"


# ---- 新补充：离线决策 Agent 的正确性 ----


def test_mock_decision_plan_works_on_custom_map():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm", map_spec=_spec())
    proposals = [mock_proposal(role, state) for role in ALL_AGENTS]
    resolution, trace, attempts, fallback = asyncio.run(decide(state, proposals))
    assert fallback is False, f"离线决策不应回退：{trace}"
    assert len(resolution.items) >= 3


def test_mock_decision_survives_zero_buses():
    state = build_scenario(seed=7, severity=3, options=ScenarioOptions(buses=0))
    proposals = [mock_proposal(role, state) for role in ALL_AGENTS]
    resolution, trace, attempts, fallback = asyncio.run(decide(state, proposals))
    assert not any("division" in line for line in trace)
    assert fallback is False
    assert resolution.items


def test_boats_add_transport_capacity():
    state = build_scenario(
        seed=7, severity=3, scenario="rainstorm", options=ScenarioOptions(buses=2, boats=2)
    )
    proposal = Proposal(
        agent="traffic",
        priority="high",
        target_area="dorm_a",
        action="evacuate",
        resource_required=ResourceRequest(buses=1, boats=2),
    )
    resolution = resolve(state, [proposal])
    assert resolution.items
    _, metrics = apply_plan(state, resolution)
    assert metrics.rescued == 25 + 2 * 10
