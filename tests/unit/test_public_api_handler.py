import json

import pytest
from conftest import api_event


@pytest.fixture
def public(stack):
    mod = stack.load("public_api.handler")
    mod.published = []
    mod.iot.publish = lambda **kw: mod.published.append(kw)
    return mod


def call(mod, method, resource, body=None, **kw):
    resp = mod.handler(api_event(method, resource, body, **kw), None)
    return resp["statusCode"], json.loads(resp["body"])


def test_report_is_published_with_hashed_phone_only(stack, public):
    status, _ = call(public, "POST", "/reports", {"zone_id": "Z07", "kind": "flooding", "severity": 3, "phone": "0771234567"})
    assert status == 202
    payload = json.loads(public.published[0]["payload"])
    assert public.published[0]["topic"] == "sm/obs/Z07"
    assert payload["source_class"] == "report" and payload["report_sev"] == 3
    assert "0771234567" not in json.dumps(payload) and len(payload["reporter_hash"]) == 24


@pytest.mark.parametrize(
    "body",
    [
        {"zone_id": "Z99", "kind": "flooding", "severity": 1, "phone": "0771234567"},  # unknown zone
        {"zone_id": "Z07", "kind": "fire", "severity": 1, "phone": "0771234567"},
        {"zone_id": "Z07", "kind": "flooding", "severity": 5, "phone": "0771234567"},
        {"zone_id": "Z07", "kind": "flooding", "severity": 1, "phone": "12"},
        {"zone_id": "Z07", "kind": "flooding", "severity": 1, "phone": "0771234567", "lat": 6.9, "lon": 80.1},
    ],
)
def test_report_validation(stack, public, body):
    assert call(public, "POST", "/reports", body)[0] == 400
    assert public.published == []


def test_fourth_report_in_ten_minutes_is_rate_limited(stack, public):
    body = {"zone_id": "Z07", "kind": "flooding", "severity": 2, "phone": "0771234567"}
    assert [call(public, "POST", "/reports", body)[0] for _ in range(4)] == [202, 202, 202, 429]


def test_public_zones_hide_test_zones(stack, public):
    for zid in ("Z07", "T01", "H00", "L01"):
        stack.table("zone-state").put_item(Item={"zone_id": zid, "tier": "watch"})
    _, body = call(public, "GET", "/public/zones")
    assert [z["zone_id"] for z in body["zones"]] == ["Z07"]


def subscribe(public, token="device-token-0123456789"):
    status, body = call(public, "POST", "/phone/subscribe", {"zone_id": "Z07", "lang": "si", "geohash6": "tc3d2x", "device_token": token})
    assert status == 201
    return body["subscriber_id"]


def test_inbox_needs_the_device_token(stack, public):
    sid = subscribe(public)
    assert call(public, "GET", "/phone/inbox", query={"subscriber_id": sid}, headers={"X-Device-Token": "wrong"})[0] == 403
    status, body = call(public, "GET", "/phone/inbox", query={"subscriber_id": sid}, headers={"X-Device-Token": "device-token-0123456789"})
    assert status == 200 and body["messages"] == []


def test_subscribe_rejects_coordinates_instead_of_geohash(stack, public):
    status, _ = call(public, "POST", "/phone/subscribe", {"zone_id": "Z07", "lang": "en", "geohash6": "6.9,80.1", "device_token": "x" * 20})
    assert status == 400


def test_ack_records_one_receipt_and_time_to_first_alert(stack, public):
    sid = subscribe(public)
    stack.table("alerts").put_item(Item={"alert_id": "A1", "sk": "META", "delivered": 0})
    stack.table("inbox").put_item(Item={"subscriber_id": sid, "sk": "1000#A1", "alert_id": "A1", "tier": "imminent", "trigger_rx_ms": 1})
    headers = {"X-Device-Token": "device-token-0123456789"}
    status, body = call(public, "POST", "/phone/ack", {"sk": "1000#A1"}, query={"subscriber_id": sid}, headers=headers)
    assert status == 200 and body["ttfa_ms"] > 0
    assert call(public, "POST", "/phone/ack", {"sk": "1000#A1"}, query={"subscriber_id": sid}, headers=headers)[1] == {"duplicate": True}
    alert = stack.table("alerts").get_item(Key={"alert_id": "A1", "sk": "META"})["Item"]
    assert alert["delivered"] == 1 and alert["first_receipt_ms"] > 0


def test_delivery_report_needs_the_shared_secret(stack, public):
    body = {"ref": "A1|S1", "status": "delivered"}
    assert call(public, "POST", "/sms/dlr", body, headers={"X-DLR-Secret": "wrong"})[0] == 403
    assert call(public, "POST", "/sms/dlr", body, headers={"X-DLR-Secret": "dlr-s3cret"})[0] == 200
