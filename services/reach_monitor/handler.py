"""Reach monitor: every minute, measure whether recent alerts actually reached people.

For each non-test alert issued in the last hour that has a planned audience, it reads the delivery ledger
and emits per tier and channel:
  DeliveryRatePct  delivered / sent, once the alert is MIN_AGE_S old (receipts take a while)
  Undelivered      sent but not (yet) confirmed
  Unsent           planned recipients the senders have not reached (audience - sent)
Real SMS ("sms") rates are what the alarm watches. Simulated deliveries ("sim") only count as delivered
when a demo phone page acknowledges them, so their rate is reported but not alarmed.

Environment: ALERTS_TABLE, DELIVERIES_TABLE, MIN_AGE_S (default 300).
"""

import os
import time
from collections import Counter
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.conditions import Key

from common.observe import log, metric

ddb = boto3.resource("dynamodb")
alerts = ddb.Table(os.environ["ALERTS_TABLE"])
deliveries = ddb.Table(os.environ["DELIVERIES_TABLE"])
WINDOW_S = 3600
MIN_AGE_S = int(os.environ.get("MIN_AGE_S", "300"))
TEST_ZONE_PREFIXES = ("T", "H", "L")


def handler(event, context):
    now = time.time()
    checked = []
    for item in recent_alerts(now):
        if item.get("status") == "Test" or str(item.get("zone_id", "")).startswith(TEST_ZONE_PREFIXES):
            continue
        if item.get("audience") is None:
            continue  # not planned yet; dispatch's own alarms cover a stuck alert
        checked.append(measure(item, now))
    return {"alerts": len(checked), "results": checked}


def recent_alerts(now):
    days = {datetime.fromtimestamp(now - s, UTC).strftime("%Y%m%d") for s in (0, WINDOW_S)}
    items = []
    for day in sorted(days):
        kwargs = {"IndexName": "by-day", "KeyConditionExpression": Key("day").eq(day) & Key("issued_at").gte(int(now - WINDOW_S))}
        while True:
            page = alerts.query(**kwargs)
            items += page["Items"]
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return items


def ledger(alert_id):
    rows, kwargs = [], {"KeyConditionExpression": Key("alert_id").eq(alert_id), "ProjectionExpression": "channel, #s",
                        "ExpressionAttributeNames": {"#s": "status"}}
    while True:
        page = deliveries.query(**kwargs)
        rows += page["Items"]
        if "LastEvaluatedKey" not in page:
            return rows
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def measure(item, now):
    tier, age = item["tier"], now - int(item["issued_at"])
    counts = Counter((r.get("channel", "sim"), r["status"]) for r in ledger(item["alert_id"]))
    result = {"alert_id": item["alert_id"], "tier": tier, "age_s": int(age), "channels": {}}
    for channel in sorted({c for c, _ in counts}):
        delivered = counts[(channel, "DELIVERED")]
        sent = delivered + counts[(channel, "SENT")] + counts[(channel, "UNDELIVERED")]
        metric("Undelivered", sent - delivered, Tier=tier, Channel=channel)
        rate = None
        if sent and age >= MIN_AGE_S:
            rate = round(100 * delivered / sent, 1)
            metric("DeliveryRatePct", rate, "Percent", Tier=tier, Channel=channel)
        result["channels"][channel] = {"sent": sent, "delivered": delivered, "rate_pct": rate}
    unsent = max(0, int(item["audience"]) - int(item.get("sent", 0)))
    metric("Unsent", unsent, Tier=tier)
    result["unsent"] = unsent
    log("reach", **result)
    return result
