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


# ---- verified phone numbers, consent, unsubscribe ----

def start(public, number="0771234567"):
    return call(public, "POST", "/phone/verify/start", {"msisdn": number})


def verified_token(public, number="0771234567"):
    status, body = start(public, number)
    assert status == 202 and body["sent"] is False and len(body["simulator_code"]) == 6
    status, body = call(public, "POST", "/phone/verify/finish", {"msisdn": number, "code": body["simulator_code"]})
    assert status == 200
    return body["verify_token"]


def sub_body(**extra):
    return {"zone_id": "Z07", "lang": "en", "geohash6": "tc3d2x", "device_token": "device-token-0123456789", **extra}


def test_a_verified_number_is_stored_with_consent(stack, public):
    token = verified_token(public)
    status, body = call(public, "POST", "/phone/subscribe", sub_body(msisdn="077 123 4567", verify_token=token))
    assert status == 201 and body["sms"] is True
    item = stack.table("subscribers").get_item(Key={"subscriber_id": body["subscriber_id"]})["Item"]
    assert item["msisdn"] == "+94771234567" and item["consent"] == public.CONSENT_VERSION and item["consent_at"] > 0


def test_a_number_without_proof_is_refused(stack, public):
    assert call(public, "POST", "/phone/subscribe", sub_body(msisdn="0771234567"))[0] == 400
    other = verified_token(public, "0779999999")
    assert call(public, "POST", "/phone/subscribe", sub_body(msisdn="0771234567", verify_token=other))[0] == 400
    expired = "1." + "0" * 64
    assert call(public, "POST", "/phone/subscribe", sub_body(msisdn="0771234567", verify_token=expired))[0] == 400


def test_wrong_codes_are_counted_and_then_the_code_is_dead(stack, public):
    status, body = start(public)
    for _ in range(5):
        assert call(public, "POST", "/phone/verify/finish", {"msisdn": "0771234567", "code": "000000"})[0] == 400
    right = call(public, "POST", "/phone/verify/finish", {"msisdn": "0771234567", "code": body["simulator_code"]})
    assert right[0] == 400 and "new one" in right[1]["error"]


def test_codes_are_limited_per_number_and_only_for_mobiles(stack, public):
    assert start(public, "0112345678")[0] == 400  # a landline
    assert [start(public)[0] for _ in range(4)] == [202, 202, 202, 429]


def test_the_allowlist_limits_real_sms_to_demo_phones(stack, public, monkeypatch):
    monkeypatch.setenv("REAL_SMS_ALLOWLIST", "0771234567, 0779999999")
    assert start(public, "0775555555")[0] == 403
    assert start(public, "0779999999")[0] == 202


def test_a_real_provider_texts_the_code_instead_of_returning_it(stack, public, monkeypatch):
    sent = []
    monkeypatch.setattr(public, "PROVIDER", "http")
    monkeypatch.setattr(public, "send_code", lambda msisdn, code: sent.append((msisdn, code)) or {"status": "SENT"})
    status, body = start(public)
    assert status == 202 and body == {"sent": True} and sent[0][0] == "+94771234567"


def test_unsubscribe_erases_the_record_and_needs_the_device(stack, public):
    _, body = call(public, "POST", "/phone/subscribe", sub_body())
    sid = body["subscriber_id"]
    assert call(public, "DELETE", "/phone/subscribe", query={"subscriber_id": sid}, headers={"X-Device-Token": "wrong"})[0] == 403
    status, out = call(public, "DELETE", "/phone/subscribe", query={"subscriber_id": sid},
                       headers={"X-Device-Token": "device-token-0123456789"})
    assert status == 200 and out == {"unsubscribed": True}
    assert "Item" not in stack.table("subscribers").get_item(Key={"subscriber_id": sid})
