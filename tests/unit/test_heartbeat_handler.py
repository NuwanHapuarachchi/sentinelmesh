import json
import time

import pytest


@pytest.fixture
def heartbeat(stack, monkeypatch):
    monkeypatch.setenv("HEARTBEAT_ZONES", "H00,H01,H02")
    mod = stack.load("heartbeat.handler")
    mod.metrics = []
    mod.metric = lambda name, value, unit="Count", **dims: mod.metrics.append((name, value, dims))
    return mod


def deliver_to_inbox(stack, heartbeat, broken=()):
    """Stand in for the whole cloud path: a published heartbeat reading lands in the zone's inbox."""
    def publish(topic, qos, payload):
        zone_id = topic.rsplit("/", 1)[1]
        if zone_id in broken:
            return
        start_ms = int(json.loads(payload)["hb_id"])
        stack.table("inbox").put_item(Item={"subscriber_id": f"heartbeat-{zone_id}", "sk": f"{start_ms + 4200}#A-{zone_id}"})
    heartbeat.iot.publish = publish


def test_every_cells_zone_beats_and_reports_per_zone(stack, heartbeat):
    deliver_to_inbox(stack, heartbeat)
    out = heartbeat.handler({}, None)
    assert out["ok"] is True
    assert {(b["zone_id"], b["cell"]) for b in out["beats"]} == {("H00", "a"), ("H01", "b"), ("H02", "c")}
    assert sorted(d["Zone"] for n, v, d in heartbeat.metrics if n == "HeartbeatOK" and v == 1) == ["H00", "H01", "H02"]
    assert all(v == 4200 for n, v, _ in heartbeat.metrics if n == "HeartbeatE2EMs")


def test_a_silent_cell_fails_only_its_own_zone(stack, heartbeat, monkeypatch):
    deliver_to_inbox(stack, heartbeat, broken={"H01"})
    real_sleep = time.sleep
    monkeypatch.setattr(heartbeat, "DEADLINE_S", 0.3)
    monkeypatch.setattr(heartbeat.time, "sleep", lambda s: real_sleep(0.05))
    out = heartbeat.handler({}, None)
    assert out["ok"] is False
    results = {b["zone_id"]: b["ok"] for b in out["beats"]}
    assert results == {"H00": True, "H01": False, "H02": True}
    assert ("HeartbeatOK", 0, {"Zone": "H01"}) in heartbeat.metrics


def test_a_beat_that_cannot_start_counts_as_missed(stack, heartbeat):
    stack.table("zones").delete_item(Key={"zone_id": "H02"})
    deliver_to_inbox(stack, heartbeat)
    out = heartbeat.handler({}, None)
    assert {b["zone_id"]: b["ok"] for b in out["beats"]} == {"H00": True, "H01": True, "H02": False}
    assert ("HeartbeatOK", 0, {"Zone": "H02"}) in heartbeat.metrics
