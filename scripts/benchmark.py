"""批量跑分：多个随机种子下的成功率、时延、指标与路线 A/B 差异。

用法：
    uv run python scripts/benchmark.py --seeds 10 --mode mock
    uv run python scripts/benchmark.py --seeds 5 --mode live --decision
    uv run python scripts/benchmark.py --http-concurrency 8

结果为 JSON，供 README 里的“工程成熟度”一节引用真实数据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

from app.agents.coordinator import run_simulation
from app.agents.prompts import SYSTEM_PROMPTS, build_user_message
from app.data.scenarios import build_scenario


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return round(ordered[index], 2)


def _prompt_chars(seed: int) -> int:
    """粗估一次模拟的输入字符数（六个角色的 system + user 提示词）。"""
    state = build_scenario(seed, severity=3)
    user = build_user_message(state)
    return sum(len(prompt) + len(user) for prompt in SYSTEM_PROMPTS.values())


async def _one(seed: int, decision: bool, objective: str) -> dict:
    started = time.perf_counter()
    payload = await run_simulation(
        seed=seed, severity=3, decision_agent=decision, scenario="rainstorm", objective=objective
    )
    elapsed = time.perf_counter() - started
    result = payload["result"]
    metrics = result["metrics"]
    decision_payload = payload.get("decision") or {}
    return {
        "seed": seed,
        "decision": decision,
        "seconds": round(elapsed, 2),
        "rescued": metrics["rescued"],
        "coverage": metrics["coverage"],
        "conflicts": metrics["conflicts"],
        "risk_covered": result["risk_covered"],
        "revisions": len(result["revisions"]),
        "fallback": decision_payload.get("fallback"),
        "attempts": decision_payload.get("attempts"),
        "output_chars": sum(len(p.get("raw", "")) for p in payload["proposals"]),
        "input_chars": _prompt_chars(seed),
    }


def _summary(rows: list[dict]) -> dict:
    if not rows:
        return {"runs": 0}
    latencies = [r["seconds"] for r in rows]
    return {
        "runs": len(rows),
        "success_rate": round(sum(1 for r in rows if r) / max(1, len(rows)), 3),
        "latency_avg": round(statistics.mean(latencies), 2) if latencies else 0,
        "latency_p95": _p95(latencies),
        "rescued_avg": round(statistics.mean(r["rescued"] for r in rows), 1),
        "coverage_avg": round(statistics.mean(r["coverage"] for r in rows), 3),
        "conflicts_avg": round(statistics.mean(r["conflicts"] for r in rows), 1),
        "risk_covered_avg": round(statistics.mean(r["risk_covered"] for r in rows), 3),
        "fallback_rate": round(
            sum(1 for r in rows if r["fallback"]) / max(1, len(rows)), 3
        ) if rows and rows[0]["decision"] else None,
        "output_chars_avg": round(statistics.mean(r["output_chars"] for r in rows), 0),
        "input_chars_avg": round(statistics.mean(r["input_chars"] for r in rows), 0),
    }


async def _http_concurrency(url: str, count: int) -> dict:
    """打本地服务，测单 worker 的并发时延（不经过浏览器，纯 HTTP）。"""
    import httpx

    async with httpx.AsyncClient(timeout=120) as client:
        async def call(i: int) -> dict:
            started = time.perf_counter()
            try:
                response = await client.post(url, params={"seed": i, "severity": 3})
                ok = response.status_code == 200
            except Exception:  # noqa: BLE001
                ok = False
            return {"seconds": round(time.perf_counter() - started, 2), "ok": ok}

        results = await asyncio.gather(*(call(i) for i in range(1, count + 1)))
    latencies = [r["seconds"] for r in results]
    return {
        "concurrency": count,
        "ok_rate": round(sum(1 for r in results if r["ok"]) / count, 3),
        "wall_seconds": round(max(latencies), 2),
        "latency_avg": round(statistics.mean(latencies), 2),
        "latency_p95": _p95(latencies),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="CampusMind 跑分")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument("--objective", default="rescue")
    parser.add_argument("--decision", action="store_true", help="同时跑路线 B")
    parser.add_argument("--http-concurrency", type=int, default=0)
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/simulate")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.mode == "mock":
        import os

        os.environ["AI_MODE"] = "mock"
        from app import config as app_config

        app_config.settings.ai_mode = "mock"
        app_config.settings.api_key = ""

    report: dict = {"mode": args.mode, "objective": args.objective}
    if args.http_concurrency:
        report["http"] = await _http_concurrency(args.url, args.http_concurrency)

    route_a = [await _one(seed, False, args.objective) for seed in range(1, args.seeds + 1)]
    report["route_a"] = {"summary": _summary(route_a), "runs": route_a}

    if args.decision:
        route_b = [await _one(seed, True, args.objective) for seed in range(1, args.seeds + 1)]
        report["route_b"] = {"summary": _summary(route_b), "runs": route_b}

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(payload)
        print(f"written {args.out}")
    else:
        print(payload)


asyncio.run(main())
