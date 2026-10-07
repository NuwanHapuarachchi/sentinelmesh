import json
from pathlib import Path

ZONES = json.loads((Path(__file__).resolve().parents[2] / "terraform" / "app" / "data" / "zones.json").read_text())


def test_eighty_demo_zones_split_across_two_cells():
    demo = [z for z in ZONES if not z["is_test"]]
    assert len(demo) == 80
    assert sum(z["owner_cell"] == "a" for z in demo) == 40
    assert sum(z["owner_cell"] == "b" for z in demo) == 40
    assert len({z["district"] for z in demo}) == 6


def test_zone_ids_are_unique():
    ids = [z["zone_id"] for z in ZONES]
    assert len(ids) == len(set(ids))


def test_thresholds_are_ordered():
    for z in ZONES:
        assert 0 < z["warning_level_m"] < z["danger_level_m"], z["zone_id"]


def test_polygons_are_closed_hexagons():
    for z in ZONES:
        poly = z["polygon"]
        assert len(poly) == 7 and poly[0] == poly[-1], z["zone_id"]


def test_test_zones_use_short_timers():
    for z in ZONES:
        if z["is_test"]:
            assert z["approval_timeout_s"] <= 30 and z["gauge_cadence_s"] <= 30
