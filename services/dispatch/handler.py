"""Dispatch (planner): consumes one cell's tier queues (imminent / warning / advisory, each with its own
concurrency so urgent alerts never wait behind advisories) and fans an alert out:

1. Cell-broadcast stand-in first: cb/{zone}.json in the web bucket; phones in the zone poll it via
   CloudFront. The same message is pushed on IoT Core topic sm/broadcast/{zone}, where the Android app
   listens (listen-only Cognito guests). Nothing on the SMS side can delay either.
2. The zone's subscribers, page by page, as batches on the tier's send queue; sender Lambdas deliver them
   in parallel (sender/handler.py). Re-planning a redelivered alert is safe: the delivery ledger stops
   anyone being sent the same alert twice.

Environment: SUBSCRIBERS_TABLE, ALERTS_TABLE, WEB_BUCKET, CELL, SEND_QUEUE_IMMINENT, SEND_QUEUE_WARNING,
SEND_QUEUE_ADVISORY, BATCH_SIZE (recipients per send message, default 50), IOT_ENDPOINT (optional).
"""

import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common.observe import log, metric

ddb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
sqs = boto3.client("sqs")
iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}") if os.environ.get("IOT_ENDPOINT") else None
subscribers = ddb.Table(os.environ["SUBSCRIBERS_TABLE"])
alerts = ddb.Table(os.environ["ALERTS_TABLE"])
SEND_QUEUES = {t: os.environ[f"SEND_QUEUE_{t.upper()}"] for t in ("imminent", "warning", "advisory")}
SEND_QUEUES["clear"] = SEND_QUEUES["advisory"]  # an all-clear is not urgent
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "50"))


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            plan(json.loads(record["body"]))
        except Exception as exc:
            log("dispatch_failed", message_id=record["messageId"], error=str(exc))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def plan(alert):
    now = time.time()
    zone_id, tier = alert["zone_id"], alert["tier"]

    s3.put_object(
        Bucket=os.environ["WEB_BUCKET"], Key=f"cb/{zone_id}.json", ContentType="application/json",
        CacheControl="max-age=2",
        Body=json.dumps({**alert, "broadcast_at": int(now)}, ensure_ascii=False).encode("utf-8"),
    )
    push_broadcast(zone_id, {**alert, "broadcast_at": int(now)})

    queue, entries, audience, batches = SEND_QUEUES[tier], [], 0, 0
    kwargs = {"IndexName": "by-zone", "KeyConditionExpression": Key("zone_id").eq(zone_id), "Limit": BATCH_SIZE}
    while True:
        page = subscribers.query(**kwargs)
        recipients = [
            {"subscriber_id": s["subscriber_id"], "lang": s.get("lang", "en"), **({"msisdn": s["msisdn"]} if s.get("msisdn") else {})}
            for s in page["Items"]
        ]
        if recipients:
            body = json.dumps({"alert": alert, "recipients": recipients}, ensure_ascii=False)
            entries.append({"Id": str(len(entries)), "MessageBody": body})
            audience, batches = audience + len(recipients), batches + 1
            if len(entries) == 10:  # SendMessageBatch takes at most 10
                send_batch(queue, entries)
                entries = []
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    if entries:
        send_batch(queue, entries)

    item = alerts.update_item(
        Key={"alert_id": alert["alert_id"], "sk": "META"},
        UpdateExpression="SET audience = :a, batches = :b, planned_at = :t, dispatched_at = :t",
        ExpressionAttributeValues={":a": audience, ":b": batches, ":t": int(now)},
        ReturnValues="ALL_NEW",
    )["Attributes"]
    # Updates to one item are serialised, so exactly one of us sees the last send land after the audience
    # is known: the planner (senders already done) or the sender that finishes last (sender/handler.py).
    if audience and int(item.get("sent", 0)) >= audience:
        metric("DrainSeconds", time.time() - now, "Seconds", Tier=tier)
    metric("DispatchLagMs", now * 1000 - alert["trigger_rx_ms"], "Milliseconds", Tier=tier)
    log("alert_planned", alert_id=alert["alert_id"], zone_id=zone_id, tier=tier, audience=audience, batches=batches)


def send_batch(queue, entries):
    failed = sqs.send_message_batch(QueueUrl=queue, Entries=entries).get("Failed", [])
    if failed:  # fail the alert message; it is re-planned and the ledger skips anyone already sent
        raise RuntimeError(f"{len(failed)} send batches not queued: {failed[0].get('Code')}")


def push_broadcast(zone_id, message):
    """Push the broadcast to app listeners in the zone (best effort: the app also reads cb/{zone}.json)."""
    if iot is None:
        return
    try:
        iot.publish(topic=f"sm/broadcast/{zone_id}", qos=1, payload=json.dumps(message, ensure_ascii=False).encode("utf-8"))
        metric("AppBroadcasts", 1, Tier=message.get("tier", "unknown"))
    except ClientError as exc:
        log("app_broadcast_failed", zone_id=zone_id, error=str(exc))
