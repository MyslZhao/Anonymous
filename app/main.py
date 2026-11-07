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
from app.data.scenarios import DEFAULT_SCENARIO, SCENARIOS, ScenarioOptions, build_scenario, list_scenarios
from app.engine.simulator import snapshot
from app.storage import init_db, list_runs

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CampusMind", version="0.2.0", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _options(scale: float, buses: int | None, boats: int | None) -> ScenarioOptions:
    return ScenarioOptions.clamp(population_scale=scale, buses=buses, boats=boats)


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


@app.get("/api/scenarios")
async def scenarios() -> dict:
    return {"scenarios": list_scenarios(), "default": DEFAULT_SCENARIO}


@app.get("/api/scenario")
async def scenario(
    seed: int = Query(7, ge=0, le=9999),
    severity: int = Query(3, ge=1, le=3),
    scenario: str = Query(DEFAULT_SCENARIO),
    scale: float = Query(1.0, ge=0.1, le=5.0),
    buses: int | None = Query(None, ge=0, le=20),
    boats: int | None = Query(None, ge=0, le=10),
) -> dict:
    state = build_scenario(seed, severity, _check_scenario(scenario), _options(scale, buses, boats))
    return {**snapshot(state), "mode": settings.mode_label}


@app.post("/api/simulate")
async def simulate(
    seed: int = Query(7, ge=0, le=9999),
    severity: int = Query(3, ge=1, le=3),
    decision_agent: bool = Query(False),
    scenario: str = Query(DEFAULT_SCENARIO),
    scale: float = Query(1.0, ge=0.1, le=5.0),
    buses: int | None = Query(None, ge=0, le=20),
    boats: int | None = Query(None, ge=0, le=10),
) -> dict:
    return await run_simulation(
        seed=seed,
        severity=severity,
        decision_agent=decision_agent,
        scenario=_check_scenario(scenario),
        options=_options(scale, buses, boats),
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
) -> StreamingResponse:
    _check_scenario(scenario)

    async def generator():
        try:
            async for event in stream_simulation(
                seed, severity, decision_agent, scenario, _options(scale, buses, boats)
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
