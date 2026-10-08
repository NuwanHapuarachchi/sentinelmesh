import json
import time
from datetime import UTC, datetime

import pytest
from conftest import api_event


@pytest.fixture
def console(stack):
    return stack.load("console_api.handler")


def call(mod, method, resource, body=None, groups="duty_officer", path=None):
    resp = mod.handler(api_event(method, resource, body, groups=groups, path=path), None)
    return resp["statusCode"], json.loads(resp["body"])


def put_alert(stack, alert_id, zone_id, status="Actual"):
    now = int(time.time())
    stack.table("alerts").put_item(Item={
        "alert_id": alert_id, "sk": "META", "day": datetime.fromtimestamp(now, UTC).strftime("%Y%m%d"),
        "issued_at": now, "zone_id": zone_id, "tier": "imminent", "status": status,
    })


def test_no_group_is_refused(stack, console):
    resp = console.handler(api_event("GET", "/zones"), None)
    assert resp["statusCode"] == 403


def test_alerts_list_hides_test_heartbeat_and_load_zones(stack, console):
    put_alert(stack, "A-real", "Z07")
    put_alert(stack, "A-drill", "Z08", status="Exercise")
    put_alert(stack, "A-hb", "H00", status="Actual")  # what a bypass-issued heartbeat used to look like
    put_alert(stack, "A-e2e", "T01", status="Exercise")
    put_alert(stack, "A-load", "L01", status="Test")
    _, body = call(console, "GET", "/alerts")
    assert sorted(a["alert_id"] for a in body["alerts"]) == ["A-drill", "A-real"]


def test_viewer_sees_counts_only(stack, console):
    put_alert(stack, "A-real", "Z07")
    stack.table("zone-state").put_item(Item={"zone_id": "Z07", "tier": "warning", "score": 1, "doc": "{}"})
    _, alerts = call(console, "GET", "/alerts", groups="viewer")
    assert alerts == {"count": 1}
    _, zones = call(console, "GET", "/zones", groups="viewer")
    assert set(zones) == {"districts"}
    assert zones["districts"]["Ratnapura"]["warning"] == 1


def test_viewer_cannot_start_drills(stack, console):
    status, _ = call(console, "POST", "/scenarios/{id}", {"zone_id": "Z07"}, groups="viewer", path={"id": "single_gauge"})
    assert status == 403


class FakeLambda:
    def __init__(self):
        self.invoked = []

    def invoke(self, FunctionName, InvocationType, Payload):
        self.invoked.append((FunctionName, json.loads(Payload)))


def owners(stack):
    return {z["zone_id"]: (z["owner_cell"], z.get("home_cell")) for z in stack.table("zones").scan()["Items"]}


def test_failover_hands_a_lost_cells_zones_to_the_standby_and_rescores_them(stack, console):
    console.lam = FakeLambda()
    before = owners(stack)
    status, body = call(console, "POST", "/cells/{cell}/failover", path={"cell": "a"})
    after = owners(stack)
    moved = [z for z, (owner, _) in before.items() if owner == "a"]
    assert status == 200 and body == {"cell": "a", "action": "failover", "to": "c", "zones_moved": len(moved)}
    assert all(after[z] == ("c", "a") for z in moved)
    assert all(after[z] == before[z] for z in before if z not in moved)  # cell b and the standby's own zone untouched
    assert console.lam.invoked == [("sentinelmesh-c-scorer", {"mode": "watchdog"})]

    status, body = call(console, "POST", "/cells/{cell}/restore", path={"cell": "a"})
    assert status == 200 and body["to"] == "a" and body["zones_moved"] == len(moved)
    assert owners(stack) == {z: (owner, None) for z, (owner, _) in before.items()}
    assert console.lam.invoked[-1] == ("sentinelmesh-a-scorer", {"mode": "watchdog"})


def test_the_standby_itself_cannot_be_failed_over(stack, console):
    console.lam = FakeLambda()
    status, body = call(console, "POST", "/cells/{cell}/failover", path={"cell": "c"})
    assert status == 400 and "a, b" in body["error"]
    assert console.lam.invoked == []


def test_restore_with_nothing_moved_invokes_nothing(stack, console):
    console.lam = FakeLambda()
    status, body = call(console, "POST", "/cells/{cell}/restore", path={"cell": "b"})
    assert status == 200 and body["zones_moved"] == 0 and console.lam.invoked == []
