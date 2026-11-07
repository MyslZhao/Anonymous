"""模拟状态与可达性测试：确认场景设计真的会制造出封路冲突。"""

from app.data.campus import build_scenario


def test_scenario_has_blocked_roads_and_trapped_people():
    state = build_scenario(seed=7, severity=3)
    assert state.severity == 3
    assert not state.edges["e_teach_dorma"]["open"]
    assert not state.edges["e_dormline"]["open"]
    assert state.total_trapped() > 100


def test_dorm_a_only_reachable_via_road2():
    """教学路与宿舍环线被淹后，宿舍A 只剩 2 号道路，这是封路冲突的前提。"""
    state = build_scenario(seed=7, severity=3)
    assert state.path_exists("dorm_a", "clinic")
    assert state.path_exists("dorm_a", "clinic", {"e_road2"}) is False


def test_isolating_edges_detects_road2():
    state = build_scenario(seed=7, severity=3)
    assert "e_road2" in state.isolating_edges()


def test_find_edge_accepts_chinese_road_name():
    state = build_scenario(seed=7, severity=3)
    assert state.find_edge("2号道路") == "e_road2"
    assert state.find_edge("不存在的路") is None


def test_seed_is_deterministic():
    a = build_scenario(seed=7, severity=3)
    b = build_scenario(seed=7, severity=3)
    assert a.to_dict()["zones"] == b.to_dict()["zones"]
