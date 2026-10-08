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
