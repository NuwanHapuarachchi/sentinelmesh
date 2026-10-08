import json

import boto3
import pytest
from botocore.stub import ANY, Stubber

from common import scoring
from scripts import replay

ZONE = {"zone_id": "Z07", "name": "Ratnapura 07", "warning_level_m": 3.0, "danger_level_m": 4.5, "gauge_cadence_s": 60,
        "approval_timeout_s": 300, "is_test": False}


def gauge(ts, level):
    return {"ts": ts, "source_class": "gauge", "level_m": level}


def test_one_gauge_alone_never_gets_past_watch():
    obs = [gauge(1000 + 60 * i, 3.0 + 0.1 * i) for i in range(10)]  # rising to 3.9 m
    assert [c["tier"] for c in replay.replay(ZONE, obs)] == ["watch"]


def test_agreement_climbs_and_the_timeline_explains_why():
    obs = [gauge(1000, 4.0), {"ts": 1060, "source_class": "forecast", "fc_rain_24h_mm": 90},
           {"ts": 1120, "source_class": "report", "report_kind": "flooding", "report_sev": 3, "reporter_hash": "a"},
           {"ts": 1121, "source_class": "report", "report_kind": "flooding", "report_sev": 3, "reporter_hash": "b"}]
    changes = replay.replay(ZONE, obs)
    assert [c["tier"] for c in changes] == ["watch", "advisory", "warning"]
    # The first report's score tips it to warning; one reporter is not yet "agreement" (that takes two).
    warning = changes[-1]
    assert warning["time"] == 1120 and warning["agreeing"] == ["forecast", "gauge"] and warning["reason"] == "corroborated"


def test_the_zone_comes_back_down_only_after_the_hold_down():
    obs = [gauge(1000, 4.6), gauge(1060, 2.0)]  # hard threshold, then the river drops
    changes = replay.replay(ZONE, obs, until=1000 + 3 * 3600)
    assert changes[0]["tier"] == "imminent" and changes[0]["reason"] == "hard_threshold"
    down = changes[1]
    assert down["from"] == "imminent" and down["time"] - 1060 >= 600  # 10-minute hold-down for real zones


def test_overrides_change_the_outcome_and_are_undone():
    obs = [gauge(1000, 4.0), {"ts": 1060, "source_class": "forecast", "fc_rain_24h_mm": 90},
           {"ts": 1120, "source_class": "report", "report_kind": "flooding", "report_sev": 3, "reporter_hash": "a"},
           {"ts": 1121, "source_class": "report", "report_kind": "flooding", "report_sev": 3, "reporter_hash": "b"}]
    before = scoring.ENTER["warning"]
    with replay.overrides(["ENTER.warning=0.9", "AGREE=0.6"]):
        assert scoring.ENTER["warning"] == 0.9 and scoring.AGREE == 0.6
        assert "warning" not in [c["tier"] for c in replay.replay(ZONE, obs)]
    assert scoring.ENTER["warning"] == before and scoring.AGREE == 0.5
    with pytest.raises(ValueError), replay.overrides(["NOPE=1"]):
        pass


def test_csv_with_iso_times(tmp_path):
    f = tmp_path / "series.csv"
    f.write_text("ts,source_class,level_m,fc_rain_24h_mm\n2026-10-09T00:00:00Z,gauge,4.6,\n2026-10-09T00:01:00Z,forecast,,120\n",
                 encoding="utf-8")
    obs = replay.read_file(f)
    assert obs[0] == {"ts": 1791504000.0, "source_class": "gauge", "level_m": 4.6}
    assert obs[1]["fc_rain_24h_mm"] == 120.0 and "level_m" not in obs[1]


def test_jsonl_and_the_command_line(tmp_path, capsys):
    f = tmp_path / "obs.jsonl"
    f.write_text("\n".join(json.dumps(gauge(1000 + 60 * i, 9.0)) for i in range(2)), encoding="utf-8")  # past Z07's danger level
    assert replay.main(["--zone", "Z07", "--file", str(f)]) == 0
    out = capsys.readouterr().out
    assert "Z07 (Ratnapura 07): 2 observations, 1 tier changes" in out and "imminent" in out


def test_athena_source():
    athena = boto3.client("athena", region_name="ap-southeast-2", aws_access_key_id="x", aws_secret_access_key="x")
    session = type("S", (), {"client": lambda self, name: athena})()
    with Stubber(athena) as stub:
        stub.add_response("start_query_execution", {"QueryExecutionId": "q1"}, {"QueryString": ANY, "WorkGroup": "sentinelmesh-replay"})
        stub.add_response("get_query_execution", {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}, {"QueryExecutionId": "q1"})
        rows = [["zone_id", "rx_ms", "ts", "source_class", "level_m", "reporter_hash"],
                ["Z07", "1791504000000", "1791504000.0", "gauge", "4.6", None]]
        result = {"ResultSet": {"Rows": [{"Data": [{"VarCharValue": v} if v else {} for v in r]} for r in rows]}}
        stub.add_response("get_query_results", result, {"QueryExecutionId": "q1"})
        obs = replay.athena_observations(session, "Z07", 1791500000, 1791510000)
    assert obs == [{"zone_id": "Z07", "rx_ms": 1791504000000.0, "ts": 1791504000.0, "source_class": "gauge", "level_m": 4.6}]
    with pytest.raises(ValueError):
        replay.athena_observations(session, "Z07'; DROP TABLE x; --", 0, 1)
