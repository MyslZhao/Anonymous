"""FastAPI 入口：页面、场景、模拟接口、SSE 事件流、历史记录。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.agents.coordinator import run_simulation, stream_simulation
from app.config import settings
from app.data.maps import MapSpec, MapValidationError, new_map_id, validate_map
from app.data.scenarios import DEFAULT_SCENARIO, SCENARIOS, ScenarioOptions, build_scenario, list_scenarios
from app.engine.rules import OBJECTIVES
from app.engine.simulator import snapshot
from app.storage import init_db, list_maps, list_runs, load_map, save_map

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CampusMind", version="0.2.0", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _check_objective(objective: str) -> str:
    if objective not in OBJECTIVES:
        raise HTTPException(status_code=400, detail=f"未知目标函数：{objective}")
    return objective


def _options(scale: float, buses: int | None, boats: int | None) -> ScenarioOptions:
    return ScenarioOptions.clamp(population_scale=scale, buses=buses, boats=boats)


def _load_map(map_id: str | None) -> tuple[str | None, MapSpec | None]:
    """按 map_id 取地图定义；不传则返回 (None, None)，走内置演示地图。"""
    if not map_id:
        return None, None
    payload = load_map(map_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"地图不存在：{map_id}")
    return map_id, MapSpec.model_validate(payload)


def _check_scenario(scenario: str) -> str:
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"未知场景：{scenario}")
    return scenario


@app.get("/")
async def index() -> FileResponse:
    page = STATIC_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=500, detail="前端页面缺失")
    return FileResponse(page)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "mode": settings.mode_label, "mock": settings.mock_mode}


@app.get("/api/objectives")
async def objectives() -> dict:
    return {
        "objectives": [
            {"id": key, "label": value["label"], "desc": value["desc"]}
            for key, value in OBJECTIVES.items()
        ],
        "default": "rescue",
    }


@app.get("/api/scenarios")
async def scenarios() -> dict:
    return {"scenarios": list_scenarios(), "default": DEFAULT_SCENARIO}


@app.get("/api/maps")
async def maps() -> dict:
    return {"maps": list_maps()}


@app.post("/api/maps", status_code=201)
async def create_map(spec: MapSpec) -> dict:
    try:
        validate_map(spec)
    except MapValidationError as exc:
        raise HTTPException(status_code=400, detail={"message": "地图校验失败", "errors": exc.errors}) from exc
    map_id = new_map_id()
    save_map(map_id, spec.name, spec.model_dump())
    return {
        "map_id": map_id,
        "name": spec.name,
        "zones": len(spec.zones),
        "edges": len(spec.edges),
        "roles": _roles_preview(spec),
    }


@app.get("/api/maps/{map_id}")
async def get_map(map_id: str) -> dict:
    payload = load_map(map_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"地图不存在：{map_id}")
    spec = MapSpec.model_validate(payload)
    return {"map_id": map_id, **spec.model_dump(), "roles": _roles_preview(spec)}


def _roles_preview(spec: MapSpec) -> dict:
    from app.data.maps import derive_roles, map_to_state_parts

    zones, _, _ = map_to_state_parts(spec)
    return derive_roles(zones)


@app.get("/api/scenario")
async def scenario(
    seed: int = Query(7, ge=0, le=9999),
    severity: int = Query(3, ge=1, le=3),
    scenario: str = Query(DEFAULT_SCENARIO),
    scale: float = Query(1.0, ge=0.1, le=5.0),
    buses: int | None = Query(None, ge=0, le=20),
    boats: int | None = Query(None, ge=0, le=10),
    objective: str = Query("rescue"),
    rounds: int = Query(1, ge=1, le=3),
    map_id: str | None = Query(None),
) -> dict:
    _, map_spec = _load_map(map_id)
    state = build_scenario(
        seed, severity, _check_scenario(scenario), _options(scale, buses, boats), map_spec
    )
    return {**snapshot(state), "mode": settings.mode_label, "map_id": map_id}


@app.post("/api/simulate")
async def simulate(
    seed: int = Query(7, ge=0, le=9999),
    severity: int = Query(3, ge=1, le=3),
    decision_agent: bool = Query(False),
    scenario: str = Query(DEFAULT_SCENARIO),
    scale: float = Query(1.0, ge=0.1, le=5.0),
    buses: int | None = Query(None, ge=0, le=20),
    boats: int | None = Query(None, ge=0, le=10),
    objective: str = Query("rescue"),
    rounds: int = Query(1, ge=1, le=3),
    map_id: str | None = Query(None),
) -> dict:
    resolved_id, map_spec = _load_map(map_id)
    return await run_simulation(
        seed=seed,
        severity=severity,
        decision_agent=decision_agent,
        scenario=_check_scenario(scenario),
        options=_options(scale, buses, boats),
        objective=_check_objective(objective),
        rounds=rounds,
        map_spec=map_spec,
        map_id=resolved_id,
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/api/simulate/stream")
async def simulate_stream(
    seed: int = Query(7, ge=0, le=9999),
    severity: int = Query(3, ge=1, le=3),
    decision_agent: bool = Query(False),
    scenario: str = Query(DEFAULT_SCENARIO),
    scale: float = Query(1.0, ge=0.1, le=5.0),
    buses: int | None = Query(None, ge=0, le=20),
    boats: int | None = Query(None, ge=0, le=10),
    objective: str = Query("rescue"),
    rounds: int = Query(1, ge=1, le=3),
    map_id: str | None = Query(None),
) -> StreamingResponse:
    _check_scenario(scenario)
    resolved_id, map_spec = _load_map(map_id)

    async def generator():
        try:
            async for event in stream_simulation(
                seed,
                severity,
                decision_agent,
                scenario,
                _options(scale, buses, boats),
                _check_objective(objective),
                rounds,
                map_spec,
                resolved_id,
            ):
                yield _sse(event)
                await asyncio.sleep(0.05)  # 让前端能真正“逐条浮现”，而不是瞬间刷完
        except Exception as exc:  # 推送到前端并保留服务端不崩
            yield _sse({"type": "error", "data": {"message": str(exc)}})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/api/history")
async def history(limit: int = Query(20, ge=1, le=100)) -> dict:
    return {"runs": list_runs(limit)}
