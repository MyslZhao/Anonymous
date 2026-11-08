"""API 层测试：页面、健康检查、场景预览、同步模拟与 SSE 事件流。"""

import json

from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_mock_mode():
    with TestClient(app) as client:
        data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert data["mock"] is True


def test_index_page_is_served():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "CampusMind" in response.text


def test_scenario_preview():
    with TestClient(app) as client:
        data = client.get("/api/scenario", params={"seed": 7, "severity": 3}).json()
    assert len(data["zones"]) == 8
    assert data["blocked_roads"]
    assert data["trapped_total"] > 0


def test_scenarios_endpoint_lists_all():
    with TestClient(app) as client:
        data = client.get("/api/scenarios").json()
    ids = {item["id"] for item in data["scenarios"]}
    assert ids == {"rainstorm", "fire", "blackout", "food_safety"}
    assert data["default"] == "rainstorm"


def test_simulate_with_fire_scenario():
    with TestClient(app) as client:
        data = client.post("/api/simulate", params={"seed": 7, "severity": 3, "scenario": "fire"}).json()
    assert data["scenario"]["event"]["id"] == "fire"
    assert data["result"]["metrics"]["conflicts"] >= 1


def test_unknown_scenario_returns_400():
    with TestClient(app) as client:
        response = client.post("/api/simulate", params={"scenario": "earthquake"})
    assert response.status_code == 400


def test_simulate_honors_scale_and_vehicle_overrides():
    with TestClient(app) as client:
        base = client.post("/api/simulate", params={"scenario": "rainstorm", "seed": 7}).json()
        tuned = client.post(
            "/api/simulate",
            params={"scenario": "rainstorm", "seed": 7, "scale": 2.0, "buses": 6, "boats": 3},
        ).json()
    assert tuned["result"]["state"]["resources"]["buses"] == 6
    assert tuned["result"]["state"]["resources"]["boats"] == 3
    assert tuned["scenario"]["trapped_total"] > base["scenario"]["trapped_total"]
    assert tuned["result"]["population_scale"] == 2.0


def test_scale_out_of_range_returns_422():
    with TestClient(app) as client:
        response = client.post("/api/simulate", params={"scale": 9})
    assert response.status_code == 422


def test_objectives_endpoint_lists_three():
    with TestClient(app) as client:
        data = client.get("/api/objectives").json()
    assert {o["id"] for o in data["objectives"]} == {"rescue", "fairness", "economy"}
    assert data["default"] == "rescue"


def test_simulate_honors_objective_and_rounds():
    with TestClient(app) as client:
        data = client.post(
            "/api/simulate", params={"objective": "fairness", "rounds": 2, "seed": 7}
        ).json()
    assert data["result"]["objective"] == "fairness"
    assert len(data["result"]["rounds"]) == 2
    assert len(data["proposals"]) == 12


def test_unknown_objective_returns_400():
    with TestClient(app) as client:
        response = client.post("/api/simulate", params={"objective": "whatever"})
    assert response.status_code == 400


def test_simulate_endpoint_returns_full_result():
    with TestClient(app) as client:
        data = client.post("/api/simulate", params={"seed": 7, "severity": 3}).json()
    assert len(data["proposals"]) == 6
    assert data["result"]["metrics"]["rescued"] > 0


def test_sse_stream_emits_staged_events():
    events = []
    with TestClient(app) as client:
        with client.stream("GET", "/api/simulate/stream", params={"seed": 7, "severity": 3}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            for line in response.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
    types = [e["type"] for e in events]
    assert types[0] == "scenario"
    assert types.count("proposal") == 6
    assert "resolution" in types
    assert types[-1] == "result"


def test_history_endpoint():
    with TestClient(app) as client:
        data = client.get("/api/history", params={"limit": 5}).json()
    assert "runs" in data


CUSTOM_MAP = {
    "name": "测试园区",
    "zones": [
        {"id": "a1", "name": "宿舍", "kind": "residential", "x": 100, "y": 100, "population": 200},
        {"id": "b1", "name": "门诊", "kind": "medical", "x": 260, "y": 120, "population": 10},
        {"id": "c1", "name": "操场", "kind": "shelter", "x": 300, "y": 260, "population": 20},
    ],
    "edges": [
        {"id": "x1", "a": "a1", "b": "b1", "name": "北街"},
        {"id": "x2", "a": "b1", "b": "c1", "name": "南街"},
        {"id": "x3", "a": "a1", "b": "c1", "name": "西街"},
    ],
}


def test_create_and_read_custom_map():
    with TestClient(app) as client:
        created = client.post("/api/maps", json=CUSTOM_MAP)
        assert created.status_code == 201
        map_id = created.json()["map_id"]
        assert created.json()["zones"] == 3
        assert created.json()["roles"]["medical"] == "b1"

        listed = client.get("/api/maps").json()["maps"]
        assert any(item["id"] == map_id for item in listed)

        fetched = client.get(f"/api/maps/{map_id}").json()
        assert fetched["name"] == "测试园区"
        assert len(fetched["zones"]) == 3


def test_invalid_map_returns_400_with_errors():
    bad = {**CUSTOM_MAP, "zones": CUSTOM_MAP["zones"][:1], "edges": []}
    with TestClient(app) as client:
        response = client.post("/api/maps", json=bad)
    assert response.status_code == 400
    assert response.json()["detail"]["errors"]


def test_unknown_map_returns_404():
    with TestClient(app) as client:
        assert client.get("/api/maps/nope").status_code == 404
        assert client.post("/api/simulate", params={"map_id": "nope"}).status_code == 404


def test_simulate_on_custom_map():
    with TestClient(app) as client:
        map_id = client.post("/api/maps", json=CUSTOM_MAP).json()["map_id"]
        data = client.post(
            "/api/simulate", params={"map_id": map_id, "scenario": "rainstorm", "seed": 7}
        ).json()
    assert data["result"]["map_id"] == map_id
    assert {zone["id"] for zone in data["scenario"]["zones"]} == {"a1", "b1", "c1"}
    assert data["result"]["metrics"]["trapped_total"] > 0
