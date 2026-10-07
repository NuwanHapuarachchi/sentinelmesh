import pytest

from common import scoring as sc

NOW = 1_800_000_000
ZONE = {"zone_id": "T01", "warning_level_m": 3.0, "danger_level_m": 4.5, "gauge_cadence_s": 60}


def gauge(level, age=0):
    return {"value": level, "ts": NOW - age}


def report(h, sev=3, kind="flooding", age=0):
    return {"h": h, "sev": sev, "kind": kind, "ts": NOW - age}


def level_for(s):
    """Gauge level giving severity s (between warning 0.6 and danger 1.0)."""
    return ZONE["warning_level_m"] + (s - 0.6) / 0.4 * (ZONE["danger_level_m"] - ZONE["warning_level_m"])


def assess(evidence=None, reports=(), history=(), last_seen=None, previous=None):
    evidence = evidence or {}
    last_seen = last_seen if last_seen is not None else {c: e["ts"] for c, e in evidence.items()}
    return sc.assess(ZONE, evidence, list(reports), list(history), last_seen, previous or {}, NOW)


def test_single_gauge_alone_is_only_watch():
    a = assess({"gauge": gauge(level_for(0.8))})
    assert a.tier == "watch"
    assert a.score == pytest.approx(0.32, abs=0.01)
    assert a.agreeing == ["gauge"]


def test_gauge_forecast_and_reports_agree_to_warning():
    a = assess(
        {"gauge": gauge(level_for(0.8)), "forecast": {"value": 90, "ts": NOW}},
        reports=[report("a"), report("b")],
    )
    assert a.tier == "warning"
    assert set(a.agreeing) == {"gauge", "forecast", "report"}


def test_adding_heavy_rain_reaches_imminent():
    a = assess(
        {
            "gauge": gauge(level_for(0.8)),
            "forecast": {"value": 90, "ts": NOW},
            "rain": {"value": 130, "ts": NOW},
        },
        reports=[report("a"), report("b")],
    )
    assert a.tier == "imminent"
    assert a.reason == "corroborated"


def test_fifty_citizen_reports_alone_stay_at_watch():
    a = assess(reports=[report(f"r{i}") for i in range(50)])
    assert a.tier == "watch"
    assert a.score <= 0.25


def test_forecast_and_reports_can_never_be_imminent():
    a = assess({"forecast": {"value": 500, "ts": NOW}}, reports=[report(f"r{i}") for i in range(20)])
    assert sc.RANK[a.tier] < sc.RANK["imminent"]


def test_fresh_gauge_at_danger_level_is_imminent_hard_threshold():
    a = assess({"gauge": gauge(ZONE["danger_level_m"])})
    assert a.tier == "imminent"
    assert a.reason == "hard_threshold"


def test_stale_gauge_at_danger_level_is_not_hard_threshold():
    a = assess({"gauge": gauge(ZONE["danger_level_m"], age=600)})
    assert a.reason != "hard_threshold"


def test_reports_from_one_reporter_count_once():
    many = assess(reports=[report("same") for _ in range(10)])
    one = assess(reports=[report("same")])
    assert many.score == one.score


def test_landslide_reports_do_not_raise_flood_and_vice_versa():
    a = assess(reports=[report("a", kind="crack"), report("b", kind="crack")])
    assert a.hazard == "landslide"


@pytest.mark.parametrize(
    ("age", "expected"),
    [(0, 1.0), (120, 1.0), (120 + 900, 0.5), (3600, 0.0)],
)
def test_gauge_freshness_decay(age, expected):
    assert sc.freshness("gauge", age, 60) == pytest.approx(expected, abs=0.01)


def test_silence_after_rising_trend_is_blind_and_escalates():
    history = [(NOW - 900 + 60 * i, 2.0 + 0.05 * i) for i in range(6)]  # rising ~3 m/h, then silent
    last_ts = history[-1][0]
    a = assess(
        {"gauge": {"value": history[-1][1], "ts": last_ts}},
        history=history,
        last_seen={"gauge": last_ts},
        previous={"tier": "watch"},
    )
    assert a.blind
    assert a.tier == "warning"
    assert a.reason == "blind_escalation"


def test_blind_never_produces_imminent():
    history = [(NOW - 900 + 60 * i, 4.0 + 0.1 * i) for i in range(6)]
    a = assess(
        {"gauge": {"value": history[-1][1], "ts": history[-1][0]}},
        history=history,
        last_seen={"gauge": history[-1][0]},
        previous={"tier": "warning"},
    )
    assert a.blind
    assert a.tier != "imminent"


def test_recent_sensor_is_not_blind():
    assert not sc.is_blind({}, {"gauge": NOW - 100}, 60, NOW)
    assert sc.is_blind({}, {"gauge": NOW - 200}, 60, NOW)
    assert not sc.is_blind({}, {"forecast": NOW - 99999}, 60, NOW)


def test_tier_rises_immediately():
    assert sc.next_tier("watch", "warning", 0.6, ["a", "b"], None, NOW, 600) == ("warning", None)


def test_tier_waits_hold_down_before_falling():
    tier, pending = sc.next_tier("warning", "watch", 0.30, ["a"], None, NOW, 600)
    assert (tier, pending) == ("warning", NOW)
    tier, pending = sc.next_tier("warning", "watch", 0.30, ["a"], NOW, NOW + 300, 600)
    assert tier == "warning"
    tier, pending = sc.next_tier("warning", "watch", 0.30, ["a"], NOW, NOW + 600, 600)
    assert (tier, pending) == ("watch", None)


def test_deadband_score_holds_current_tier():
    assert sc.next_tier("warning", "advisory", 0.50, ["a", "b"], None, NOW, 600) == ("warning", None)


def test_execution_name_is_deterministic_and_short():
    assert sc.execution_name("Z01", "warning", 3) == "Z01-warning-e3"
    assert sc.execution_name("Z01", "warning", 3, drill=True) == "Z01-warning-e3-d"
    assert len(sc.execution_name("Z" * 100, "imminent", 1)) <= 80
