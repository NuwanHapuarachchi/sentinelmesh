"""Dispatch (planner) -> send queue -> sender, with the delivery ledger and receipts."""

import json
import time

import pytest
from conftest import api_event

from common import sms


def alert(alert_id="SM-1", zone_id="Z07", tier="imminent"):
    return {"type": "alert", "alert_id": alert_id, "zone_id": zone_id, "tier": tier, "hazard": "flood", "status": "Exercise",
            "texts": {"en": "EMERGENCY en", "si": "EMERGENCY si", "ta": "EMERGENCY ta"}, "trigger_rx_ms": int(time.time() * 1000) - 5000}


def subscribe(stack, n, zone_id="Z07", lang="si", msisdn=None):
    with stack.table("subscribers").batch_writer() as batch:
        for i in range(n):
            batch.put_item(Item={"subscriber_id": f"{zone_id}-s{i:04d}", "zone_id": zone_id, "lang": lang, "geohash6": "tc3d2x",
                                 **({"msisdn": msisdn} if msisdn else {})})


def put_alert_meta(stack, a):
    stack.table("alerts").put_item(Item={"alert_id": a["alert_id"], "sk": "META", "sent": 0, "delivered": 0, "tier": a["tier"]})


@pytest.fixture
def planner(stack, monkeypatch):
    monkeypatch.setenv("BATCH_SIZE", "50")
    mod = stack.load("dispatch.handler")
    mod.metrics = []
    mod.metric = lambda name, value, unit="Count", **dims: mod.metrics.append((name, value, dims))
    return mod


@pytest.fixture
def sender(stack):
    mod = stack.load("sender.handler")
    mod.metrics = []
    mod.metric = lambda name, value, unit="Count", **dims: mod.metrics.append((name, value, dims))
    return mod


def sqs_event(*bodies):
    return {"Records": [{"messageId": f"m{i}", "body": json.dumps(b)} for i, b in enumerate(bodies)]}


def test_planner_broadcasts_first_then_batches_the_audience(stack, planner):
    a = alert()
    put_alert_meta(stack, a)
    subscribe(stack, 120)
    subscribe(stack, 3, zone_id="Z08")  # another zone's subscribers are not planned
    assert planner.handler(sqs_event(a), None) == {"batchItemFailures": []}

    cb = json.loads(stack.body("WEB_BUCKET", "cb/Z07.json"))
    assert cb["alert_id"] == "SM-1" and cb["broadcast_at"] > 0
    batches = stack.messages("imminent", stack.send_queues)
    assert [len(b["recipients"]) for b in batches] == [50, 50, 20]
    assert len({r["subscriber_id"] for b in batches for r in b["recipients"]}) == 120
    assert all(b["alert"] == a for b in batches)
    meta = stack.table("alerts").get_item(Key={"alert_id": "SM-1", "sk": "META"})["Item"]
    assert meta["audience"] == 120 and meta["batches"] == 3 and meta["planned_at"] > 0


def test_planner_uses_the_tiers_own_send_queue(stack, planner):
    a = alert(tier="advisory")
    put_alert_meta(stack, a)
    subscribe(stack, 2)
    planner.handler(sqs_event(a), None)
    assert stack.messages("imminent", stack.send_queues) == []
    assert len(stack.messages("advisory", stack.send_queues)) == 1


def test_sender_delivers_once_even_when_the_batch_is_replayed(stack, sender):
    a = alert()
    put_alert_meta(stack, a)
    batch = {"alert": a, "recipients": [{"subscriber_id": f"s{i}", "lang": "si"} for i in range(30)]}
    assert sender.handler(sqs_event(batch), None) == {"batchItemFailures": []}
    assert sender.handler(sqs_event(batch, batch), None) == {"batchItemFailures": []}  # redelivered twice more

    inbox = stack.table("inbox").scan()["Items"]
    assert len(inbox) == 30 and {m["text"] for m in inbox} == {"EMERGENCY si"}
    ledger = stack.table("deliveries").scan()["Items"]
    assert len(ledger) == 30 and {d["status"] for d in ledger} == {"SENT"}
    assert all(d["inbox_sk"].endswith("#SM-1") for d in ledger)
    assert stack.table("alerts").get_item(Key={"alert_id": "SM-1", "sk": "META"})["Item"]["sent"] == 30


def test_transient_failures_are_retried_and_rejections_are_not(stack, monkeypatch):
    monkeypatch.setenv("SMS_PROVIDER", "http")
    monkeypatch.setenv("SMS_HTTP_URL", "https://gw.example/send")
    mod = stack.load("sender.handler")
    mod.metric = lambda *a, **k: None
    outcomes = {"+94770000001": "SENT", "+94770000002": "FAILED", "+94770000003": "REJECTED"}
    calls = []

    def fake_send(to, text, ref, secret):
        calls.append(to)
        return {"status": outcomes[to], "provider_id": "p1"} if outcomes[to] == "SENT" else {"status": outcomes[to], "error": "x"}

    monkeypatch.setattr(sms, "send_http", fake_send)
    a = alert()
    put_alert_meta(stack, a)
    batch = {"alert": a, "recipients": [{"subscriber_id": f"s{i}", "lang": "en", "msisdn": n} for i, n in enumerate(outcomes, 1)]}

    assert mod.handler(sqs_event(batch), None) == {"batchItemFailures": [{"itemIdentifier": "m0"}]}
    outcomes["+94770000002"] = "SENT"  # the gateway recovers; SQS redelivers the batch
    assert mod.handler(sqs_event(batch), None) == {"batchItemFailures": []}

    assert sorted(calls) == ["+94770000001", "+94770000002", "+94770000002", "+94770000003"]
    ledger = {d["subscriber_id"]: d["status"] for d in stack.table("deliveries").scan()["Items"]}
    assert ledger == {"s1": "SENT", "s2": "SENT", "s3": "REJECTED"}
    assert stack.table("alerts").get_item(Key={"alert_id": "SM-1", "sk": "META"})["Item"]["sent"] == 2


def test_an_abandoned_claim_is_taken_over_but_a_fresh_one_is_not(stack, sender):
    a = alert()
    put_alert_meta(stack, a)
    now = int(time.time())
    stack.table("deliveries").put_item(Item={"alert_id": "SM-1", "subscriber_id": "fresh", "status": "SENDING", "sent_at": now})
    stack.table("deliveries").put_item(Item={"alert_id": "SM-1", "subscriber_id": "stale", "status": "SENDING", "sent_at": now - 600})
    batch = {"alert": a, "recipients": [{"subscriber_id": "fresh"}, {"subscriber_id": "stale"}]}
    sender.handler(sqs_event(batch), None)
    ledger = {d["subscriber_id"]: d["status"] for d in stack.table("deliveries").scan()["Items"]}
    assert ledger == {"fresh": "SENDING", "stale": "SENT"}


def test_drain_time_is_reported_once_by_the_batch_that_completes_the_audience(stack, sender):
    a = alert()
    stack.table("alerts").put_item(Item={"alert_id": "SM-1", "sk": "META", "sent": 0, "audience": 4, "planned_at": int(time.time()) - 3})
    one = {"alert": a, "recipients": [{"subscriber_id": "s1"}, {"subscriber_id": "s2"}]}
    two = {"alert": a, "recipients": [{"subscriber_id": "s3"}, {"subscriber_id": "s4"}]}
    sender.handler(sqs_event(one), None)
    assert not [m for m in sender.metrics if m[0] == "DrainSeconds"]
    sender.handler(sqs_event(two), None)
    [(_, seconds, dims)] = [m for m in sender.metrics if m[0] == "DrainSeconds"]
    assert seconds >= 3 and dims == {"Tier": "imminent"}


def test_planner_reports_drain_when_senders_finished_first(stack, planner):
    a = alert()
    stack.table("alerts").put_item(Item={"alert_id": "SM-1", "sk": "META", "sent": 2})
    subscribe(stack, 2)
    planner.handler(sqs_event(a), None)
    assert [m[0] for m in planner.metrics].count("DrainSeconds") == 1


def test_phone_ack_and_sms_report_mark_the_ledger_delivered(stack, sender):
    public = stack.load("public_api.handler")
    a = alert()
    put_alert_meta(stack, a)
    stack.table("subscribers").put_item(Item={"subscriber_id": "s1", "zone_id": "Z07", "lang": "en", "geohash6": "tc3d2x",
                                              "device_token_sha256": "x"})
    sender.handler(sqs_event({"alert": a, "recipients": [{"subscriber_id": "s1"}, {"subscriber_id": "s2"}]}), None)

    body = {"ref": "SM-1|s2", "status": "delivered"}
    resp = public.handler(api_event("POST", "/sms/dlr", body, headers={"X-DLR-Secret": "dlr-s3cret"}), None)
    assert resp["statusCode"] == 200 and json.loads(resp["body"])["acknowledged"] is True
    ledger = {d["subscriber_id"]: d for d in stack.table("deliveries").scan()["Items"]}
    assert ledger["s2"]["status"] == "DELIVERED" and ledger["s2"]["delivered_at"] > 0
    assert ledger["s1"]["status"] == "SENT"
    assert stack.table("alerts").get_item(Key={"alert_id": "SM-1", "sk": "META"})["Item"]["delivered"] == 1


def test_an_all_clear_is_planned_onto_the_advisory_send_queue(stack, planner):
    a = alert(tier="clear")
    put_alert_meta(stack, a)
    subscribe(stack, 1)
    planner.handler(sqs_event(a), None)
    assert len(stack.messages("advisory", stack.send_queues)) == 1


def test_a_failed_delivery_report_marks_the_ledger_undelivered(stack, sender):
    public = stack.load("public_api.handler")
    a = alert()
    put_alert_meta(stack, a)
    sender.handler(sqs_event({"alert": a, "recipients": [{"subscriber_id": "s1"}]}), None)
    resp = public.handler(api_event("POST", "/sms/dlr", {"ref": "SM-1|s1", "status": "undeliv"},
                                    headers={"X-DLR-Secret": "dlr-s3cret"}), None)
    assert json.loads(resp["body"]) == {"status": "FAILED"}
    row = stack.table("deliveries").get_item(Key={"alert_id": "SM-1", "subscriber_id": "s1"})["Item"]
    assert row["status"] == "UNDELIVERED" and row["network_status"] == "FAILED"
