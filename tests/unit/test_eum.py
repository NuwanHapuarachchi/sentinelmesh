"""AWS End User Messaging SMS: sending (shapes checked by botocore's Stubber against the real API model) and
delivery events turned into receipts."""

import json
import time

import boto3
import pytest
from botocore.stub import Stubber

from common import sms


@pytest.fixture
def eum():
    client = boto3.client("pinpoint-sms-voice-v2", region_name="ap-southeast-2", aws_access_key_id="x", aws_secret_access_key="x")
    with Stubber(client) as stub:
        yield client, stub


def test_send_puts_our_reference_in_the_context(eum):
    client, stub = eum
    stub.add_response("send_text_message", {"MessageId": "m-1"}, {
        "DestinationPhoneNumber": "+94771234567", "MessageBody": "hello", "MessageType": "TRANSACTIONAL",
        "Context": {"ref": "SM-1|s1"}, "OriginationIdentity": "SentinelMsh", "ConfigurationSetName": "alerts",
    })
    assert sms.send_eum("0771234567", "hello", "SM-1|s1", client, "SentinelMsh", "alerts") == {"status": "SENT", "provider_id": "m-1"}
    stub.assert_no_pending_responses()


@pytest.mark.parametrize(("code", "status"), [("ThrottlingException", "FAILED"), ("InternalServerException", "FAILED"),
                                              ("ValidationException", "REJECTED"), ("AccessDeniedException", "REJECTED")])
def test_errors_are_transient_or_permanent(eum, code, status):
    client, stub = eum
    stub.add_client_error("send_text_message", service_error_code=code)
    assert sms.send_eum("0771234567", "x", "r", client) == {"status": status, "error": code}


@pytest.mark.parametrize(("event", "expected"), [
    ({"messageStatus": "DELIVERED", "context": {"ref": "A|s"}}, ("A|s", "DELIVERED", "DELIVERED")),
    ({"messageStatus": "SUCCESSFUL", "context": {"ref": "A|s"}}, ("A|s", None, "SUCCESSFUL")),  # carrier only
    ({"messageStatus": "CARRIER_BLOCKED", "context": {"ref": "A|s"}}, ("A|s", "FAILED", "CARRIER_BLOCKED")),
    ({"messageStatus": "UNKNOWN", "isFinal": True, "context": {"ref": "A|s"}}, ("A|s", "FAILED", "UNKNOWN")),
    ({"messageStatus": "UNKNOWN", "isFinal": False}, (None, None, "UNKNOWN")),
])
def test_event_outcomes(event, expected):
    assert sms.parse_eum_event(event) == expected


def sns(*events):
    return {"Records": [{"Sns": {"Message": json.dumps(e)}} for e in events]}


def test_sender_uses_end_user_messaging_for_real_numbers(stack, monkeypatch, eum):
    client, stub = eum
    monkeypatch.setenv("SMS_PROVIDER", "eum")
    monkeypatch.setenv("EUM_CONFIGURATION_SET", "alerts")
    sender = stack.load("sender.handler")
    sender.eum = client
    stub.add_response("send_text_message", {"MessageId": "m-9"}, {
        "DestinationPhoneNumber": "+94770000001", "MessageBody": "EMERGENCY en", "MessageType": "TRANSACTIONAL",
        "Context": {"ref": "SM-1|s1"}, "ConfigurationSetName": "alerts",
    })
    stack.table("alerts").put_item(Item={"alert_id": "SM-1", "sk": "META", "sent": 0})
    alert = {"alert_id": "SM-1", "zone_id": "Z07", "tier": "imminent", "status": "Exercise", "trigger_rx_ms": 1,
             "texts": {"en": "EMERGENCY en", "si": "si", "ta": "ta"}}
    body = {"alert": alert, "recipients": [{"subscriber_id": "s1", "lang": "en", "msisdn": "+94770000001"},
                                           {"subscriber_id": "s2", "lang": "en"}]}
    sender.handler({"Records": [{"messageId": "m", "body": json.dumps(body)}]}, None)
    stub.assert_no_pending_responses()  # exactly one real SMS; the handset without a number got the simulator
    rows = {r["subscriber_id"]: r for r in stack.table("deliveries").scan()["Items"]}
    assert (rows["s1"]["channel"], rows["s1"]["provider_id"]) == ("sms", "m-9") and rows["s2"]["channel"] == "sim"


def test_delivery_events_become_receipts(stack):
    events = stack.load("sms_events.handler")
    now = int(time.time())
    stack.table("alerts").put_item(Item={"alert_id": "SM-1", "sk": "META", "delivered": 0})
    for sid in ("s1", "s2"):
        stack.table("inbox").put_item(Item={"subscriber_id": sid, "sk": f"{now * 1000}#SM-1", "alert_id": "SM-1",
                                            "tier": "imminent", "trigger_rx_ms": now * 1000 - 30_000})
        stack.table("deliveries").put_item(Item={"alert_id": "SM-1", "subscriber_id": sid, "status": "SENT",
                                                 "channel": "sms", "inbox_sk": f"{now * 1000}#SM-1"})
    out = events.handler(sns({"messageStatus": "DELIVERED", "context": {"ref": "SM-1|s1"}, "messageId": "m1"},
                             {"messageStatus": "DELIVERED", "context": {"ref": "SM-1|s1"}, "messageId": "m1"},
                             {"messageStatus": "SUCCESSFUL", "context": {"ref": "SM-1|s2"}},
                             {"messageStatus": "UNREACHABLE", "isFinal": True, "context": {"ref": "SM-1|s2"},
                              "messageStatusDescription": "Phone off"}), None)["outcomes"]
    assert out[0]["acknowledged"] and out[1] == {"duplicate": True} and out[2] == {"progress": "SUCCESSFUL"}
    rows = {r["subscriber_id"]: r for r in stack.table("deliveries").scan()["Items"]}
    assert rows["s1"]["status"] == "DELIVERED"
    assert (rows["s2"]["status"], rows["s2"]["network_status"], rows["s2"]["error"]) == ("UNDELIVERED", "UNREACHABLE", "Phone off")
    assert stack.table("alerts").get_item(Key={"alert_id": "SM-1", "sk": "META"})["Item"]["delivered"] == 1
