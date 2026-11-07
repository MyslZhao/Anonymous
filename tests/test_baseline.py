"""无干预基线（反事实对照）测试。"""

from app.data.scenarios import build_scenario
from app.engine.simulator import BASELINE_WINDOW_MINUTES, baseline_outcome, risk_coverage
from app.models import Metrics


def test_baseline_reports_exposure_and_casualties():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    baseline = baseline_outcome(state)
    assert baseline["window_minutes"] == BASELINE_WINDOW_MINUTES
    assert baseline["trapped_total"] == state.total_trapped()
    assert 0 < baseline["casualties"] < baseline["at_risk"] <= state.total_trapped()
    assert baseline["isolated_zones"] == 0


def test_isolated_zone_increases_exposure():
    state = build_scenario(seed=7, severity=3, scenario="rainstorm")
    before = baseline_outcome(state)
    state.edges["e_road2"].update(open=False, reason="测试：切断宿舍A唯一通道")
    after = baseline_outcome(state)
    assert after["isolated_zones"] >= 1
    assert after["at_risk"] > before["at_risk"]


def test_baseline_scales_with_population_scale():
    from app.data.scenarios import ScenarioOptions

    base = baseline_outcome(build_scenario(seed=7, severity=3))
    big = baseline_outcome(build_scenario(seed=7, severity=3, options=ScenarioOptions(population_scale=2.0)))
    assert big["at_risk"] > base["at_risk"]


def test_risk_coverage_is_clamped():
    assert risk_coverage({"at_risk": 0}, Metrics()) == 0.0
    assert risk_coverage({"at_risk": 10}, Metrics(rescued=100)) == 1.0
    partial = risk_coverage({"at_risk": 100}, Metrics(rescued=30, stabilized=20))
    assert 0.4 < partial < 0.6


def test_baseline_grows_with_severity():
    low = baseline_outcome(build_scenario(seed=7, severity=1))
    high = baseline_outcome(build_scenario(seed=7, severity=3))
    assert high["at_risk"] > low["at_risk"]
    assert high["casualties"] >= low["casualties"]
