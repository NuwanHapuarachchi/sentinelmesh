import json
import time
from decimal import Decimal

import pytest
from boto3.dynamodb.types import TypeSerializer

_ser = TypeSerializer()


class FakeSfn:
    def __init__(self):
        self.started = []

    def start_execution(self, stateMachineArn, name, input):
        self.started.append((name, json.loads(input)))


@pytest.fixture
def scorer(stack):
    mod = stack.load("scorer.handler")
    mod.sfn = FakeSfn()
    mod.STATE_MACHINE_ARN = "arn:aws:states:ap-southeast-2:123456789012:stateMachine:t-a-escalation"
    mod.metrics = []
    mod.metric = lambda name, value, unit="Count", **dims: mod.metrics.append((name, value, dims))
    return mod


def record(zone_id, seq, **obs):
    """A DynamoDB stream record for one observation, as the IoT rule writes it."""
    now_ms = int(time.time() * 1000)
    item = {"zone_id": zone_id, "sk": f"{now_ms}#{obs.get('source_class')}#{seq}", "rx_ms": now_ms, "ts": Decimal(str(time.time())), **obs}
    image = {k: _ser.serialize(Decimal(str(v)) if isinstance(v, float) else v) for k, v in item.items()}
    return {"dynamodb": {"NewImage": image, "Keys": {"zone_id": {"S": zone_id}, "sk": image["sk"]}, "SequenceNumber": str(seq)}}


def gauge_level(stack, zone_id, severity):
    z = stack.table("zones").get_item(Key={"zone_id": zone_id})["Item"]
    w, d = float(z["warning_level_m"]), float(z["danger_level_m"])
    return round(w + (severity - 0.6) / 0.4 * (d - w), 2)


def state(stack, zone_id):
    return stack.table("zone-state").get_item(Key={"zone_id": zone_id}).get("Item") or {}


def test_single_gauge_is_watch_and_starts_nothing(stack, scorer):
    out = scorer.handler({"Records": [record("Z07", 1, source_class="gauge", level_m=gauge_level(stack, "Z07", 0.8))]}, None)
    assert out == {"batchItemFailures": []}
    assert state(stack, "Z07")["tier"] == "watch"
    assert scorer.sfn.started == []


def test_corroboration_starts_one_named_escalation(stack, scorer):
    records = [
        record("Z07", 1, source_class="gauge", level_m=gauge_level(stack, "Z07", 0.8)),
        record("Z07", 2, source_class="forecast", fc_rain_24h_mm=90.0),
        record("Z07", 3, source_class="report", reporter_hash="r1", report_kind="flooding", report_sev=3),
        record("Z07", 4, source_class="report", reporter_hash="r2", report_kind="flooding", report_sev=3),
    ]
    scorer.handler({"Records": records}, None)
    st = state(stack, "Z07")
    assert st["tier"] == "warning"
    [(name, payload)] = scorer.sfn.started
    assert name.startswith("Z07-warning-e1-")
    assert payload["tier"] == "warning" and payload["approval_timeout_s"] == 300 and payload["is_test"] is False
    assert set(payload["agreeing"]) == {"gauge", "forecast", "report"}


def test_gate_only_record_does_not_crash_the_scorer(stack, scorer):
    # The workflow's gate can create a zone-state item holding only alert_* fields (regression for 8914775).
    stack.table("zone-state").put_item(Item={"zone_id": "Z07", "alert_rank": 3, "alert_tier": "warning", "alert_exec": "x"})
    scorer.handler({"Records": [record("Z07", 1, source_class="gauge", level_m=1.0)]}, None)
    assert state(stack, "Z07")["tier"] == "normal"


def test_failure_in_one_zone_retries_only_that_zone(stack, scorer):
    original = scorer.score_zone

    def flaky(zone_id, *args, **kwargs):
        if zone_id == "Z08":
            raise RuntimeError("boom")
        return original(zone_id, *args, **kwargs)

    scorer.score_zone = flaky
    out = scorer.handler({"Records": [record("Z07", 1, source_class="gauge", level_m=1.0),
                                      record("Z08", 2, source_class="gauge", level_m=1.0)]}, None)
    assert out == {"batchItemFailures": [{"itemIdentifier": "2"}]}
    assert state(stack, "Z07")["tier"] == "normal"


def test_blind_zones_metric_counts_real_zones_only(stack, scorer):
    long_ago = time.time() - 3600
    blind_doc = json.dumps({"ev": {"gauge": {"value": 1.0, "ts": long_ago, "src": "g"}}, "last_seen": {"gauge": long_ago},
                            "gauge_hist": [], "reports": [], "score_hist": [], "pending_down_since": None, "episode": 0})
    for zone_id in ("Z07", "L01", "L02", "T01"):  # one real zone and three hidden ones, all owned by cell a
        stack.table("zone-state").put_item(Item={"zone_id": zone_id, "tier": "normal", "version": 1, "doc": blind_doc})
    out = scorer.handler({"mode": "watchdog"}, None)
    assert out["blind"] == 1 and out["blind_test_zones"] == 3
    assert ("BlindZones", 1, {"Cell": "a"}) in scorer.metrics


class FakeLambda:
    def __init__(self):
        self.invoked = []

    def invoke(self, FunctionName, InvocationType, Payload):
        self.invoked.append((FunctionName, InvocationType))


def test_a_tier_change_refreshes_the_public_map_once_per_batch(stack, scorer):
    scorer.lam, scorer.PUBLIC_FEED_FUNCTION = FakeLambda(), "t-public-feed"
    level = gauge_level(stack, "Z07", 0.8)
    scorer.handler({"Records": [record("Z07", 1, source_class="gauge", level_m=level),
                                record("Z08", 2, source_class="gauge", level_m=gauge_level(stack, "Z08", 0.8))]}, None)
    assert scorer.lam.invoked == [("t-public-feed", "Event")]
    scorer.handler({"Records": [record("Z07", 3, source_class="gauge", level_m=level)]}, None)  # still watch
    assert len(scorer.lam.invoked) == 1


def test_test_zone_changes_do_not_refresh_the_public_map(stack, scorer):
    scorer.lam, scorer.PUBLIC_FEED_FUNCTION = FakeLambda(), "t-public-feed"
    scorer.handler({"Records": [record("T01", 1, source_class="gauge", level_m=gauge_level(stack, "T01", 0.8))]}, None)
    assert state(stack, "T01")["tier"] == "watch"
    assert scorer.lam.invoked == []
