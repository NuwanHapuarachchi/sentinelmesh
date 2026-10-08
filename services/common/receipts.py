"""Delivery receipts, the same way whichever channel confirms them: a phone page acknowledging, a third-party
gateway's delivery report, or an AWS End User Messaging delivery event.

A receipt is counted once per message (the inbox item's acked_at), marks the ledger row DELIVERED, adds to
the alert's delivered count, and the first one for an alert gives its time to first alert (from the
triggering reading to a confirmed delivery).
"""

import time
from decimal import Decimal

from botocore.exceptions import ClientError

from common.observe import metric


def _conditional(exc):
    return exc.response["Error"]["Code"] == "ConditionalCheckFailedException"


def record(inbox, deliveries, alerts, subscriber_id, inbox_sk, channel, now=None):
    """Returns {"acknowledged": True, "ttfa_ms": n} or {"duplicate": True}."""
    now = now or time.time()
    try:
        item = inbox.update_item(
            Key={"subscriber_id": subscriber_id, "sk": inbox_sk}, UpdateExpression="SET acked_at = :t",
            ConditionExpression="attribute_exists(sk) AND attribute_not_exists(acked_at)",
            ExpressionAttributeValues={":t": int(now * 1000)}, ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as exc:
        if _conditional(exc):
            return {"duplicate": True}
        raise
    ttfa_ms = now * 1000 - int(item["trigger_rx_ms"])
    try:
        deliveries.update_item(
            Key={"alert_id": item["alert_id"], "subscriber_id": subscriber_id},
            UpdateExpression="SET #s = :d, delivered_at = :t", ConditionExpression="attribute_exists(subscriber_id)",
            ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":d": "DELIVERED", ":t": int(now)},
        )
    except ClientError as exc:  # no ledger row (sent before the ledger existed): the inbox receipt still counts
        if not _conditional(exc):
            raise
    first = alerts.update_item(
        Key={"alert_id": item["alert_id"], "sk": "META"},
        UpdateExpression="ADD delivered :one SET first_receipt_ms = if_not_exists(first_receipt_ms, :ms)",
        ExpressionAttributeValues={":one": 1, ":ms": Decimal(int(ttfa_ms))}, ReturnValues="ALL_NEW",
    )["Attributes"]
    metric("DeliveryReceipts", 1, Channel=channel, Tier=item["tier"])
    if int(first.get("delivered", 0)) == 1:  # first confirmed delivery for this alert
        metric("TimeToFirstAlertMs", ttfa_ms, "Milliseconds", Tier=item["tier"])
    return {"acknowledged": True, "ttfa_ms": int(ttfa_ms)}


def record_for_ref(inbox, deliveries, alerts, ref, channel, now=None):
    """A receipt that names the message by our send reference ("<alert_id>|<subscriber_id>")."""
    if not ref or "|" not in ref:
        return {"error": "missing ref"}
    alert_id, subscriber_id = ref.split("|", 1)
    sent = deliveries.get_item(Key={"alert_id": alert_id, "subscriber_id": subscriber_id}).get("Item") or {}
    if not sent.get("inbox_sk"):
        return {"unknown": True}
    return record(inbox, deliveries, alerts, subscriber_id, sent["inbox_sk"], channel, now)


def undelivered(deliveries, ref, status, reason=""):
    """A final failure from the network (invalid number, blocked, expired...): kept on the ledger row."""
    if not ref or "|" not in ref:
        return False
    alert_id, subscriber_id = ref.split("|", 1)
    try:
        deliveries.update_item(
            Key={"alert_id": alert_id, "subscriber_id": subscriber_id},
            UpdateExpression="SET #s = :u, network_status = :n, #e = :r",
            ConditionExpression="attribute_exists(subscriber_id) AND #s <> :d",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":u": "UNDELIVERED", ":n": status, ":r": reason or status, ":d": "DELIVERED"},
        )
        return True
    except ClientError as exc:
        if _conditional(exc):
            return False
        raise
