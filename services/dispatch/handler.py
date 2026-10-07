"""Dispatch: consumes one cell's tier queues (imminent / warning / advisory, each with its own
concurrency so urgent alerts never wait behind advisories) and delivers an alert:

1. Cell-broadcast stand-in: cb/{zone}.json in the web bucket; phones in the zone poll it via CloudFront.
2. Every subscriber in the zone, in their language, through the SMS adapter
   (simulator -> phone inbox; http -> the third-party gateway).

Environment: SUBSCRIBERS_TABLE, INBOX_TABLE, ALERTS_TABLE, WEB_BUCKET, CELL, SMS_PROVIDER,
SMS_SECRET_ARN, plus SMS_HTTP_* (see common/sms.py).
"""

import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Key

from common import sms
from common.observe import log, metric

ddb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")
subscribers = ddb.Table(os.environ["SUBSCRIBERS_TABLE"])
inbox = ddb.Table(os.environ["INBOX_TABLE"])
alerts = ddb.Table(os.environ["ALERTS_TABLE"])
PROVIDER = os.environ.get("SMS_PROVIDER", "simulator")
_secret_cache = {}


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            deliver(json.loads(record["body"]))
        except Exception as exc:
            log("dispatch_failed", message_id=record["messageId"], error=str(exc))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def deliver(alert):
    now = time.time()
    zone_id, tier = alert["zone_id"], alert["tier"]

    s3.put_object(
        Bucket=os.environ["WEB_BUCKET"], Key=f"cb/{zone_id}.json", ContentType="application/json",
        CacheControl="max-age=2",
        Body=json.dumps({**alert, "broadcast_at": int(now)}, ensure_ascii=False).encode("utf-8"),
    )

    recipients = query_zone(zone_id)
    sent = 0
    for sub in recipients:
        text = alert["texts"].get(sub.get("lang", "en"), alert["texts"]["en"])
        ref = f"{alert['alert_id']}|{sub['subscriber_id']}"
        if PROVIDER == "http" and sub.get("msisdn"):
            result = sms.send_http(sub["msisdn"], text, ref, load_secret())
            channel = "sms"
        else:
            result = {"status": "SENT"}
            channel = "sim"
        if channel == "sim" or result["status"] == "SENT":
            inbox.put_item(Item={
                "subscriber_id": sub["subscriber_id"], "sk": f"{int(now * 1000)}#{alert['alert_id']}",
                "alert_id": alert["alert_id"], "tier": tier, "text": text, "lang": sub.get("lang", "en"),
                "channel": channel, "status": alert.get("status", "Actual"),
                "trigger_rx_ms": alert["trigger_rx_ms"], "expires_at": int(now) + 2 * 86400,
            })
            sent += 1
        metric("Sent", 1, Channel=channel, Tier=tier, Result=result["status"])

    alerts.update_item(
        Key={"alert_id": alert["alert_id"], "sk": "META"},
        UpdateExpression="SET sent = sent + :n, dispatched_at = :t",
        ExpressionAttributeValues={":n": sent, ":t": int(now)},
    )
    metric("DispatchLagMs", now * 1000 - alert["trigger_rx_ms"], "Milliseconds", Tier=tier)
    log("alert_dispatched", alert_id=alert["alert_id"], zone_id=zone_id, tier=tier, recipients=len(recipients), sent=sent)


def query_zone(zone_id):
    items, kwargs = [], {"IndexName": "by-zone", "KeyConditionExpression": Key("zone_id").eq(zone_id)}
    while True:
        page = subscribers.query(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def load_secret():
    if "v" not in _secret_cache:
        _secret_cache["v"] = json.loads(secrets.get_secret_value(SecretId=os.environ["SMS_SECRET_ARN"])["SecretString"])
    return _secret_cache["v"]
