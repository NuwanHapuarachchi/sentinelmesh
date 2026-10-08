"""Sender: consumes one cell's send queues (one per tier, each with its own concurrency sized to the SMS
provider's throughput) and delivers one batch of recipients planned by dispatch.

Every recipient goes through the delivery ledger first (deliveries table, one item per alert and
subscriber): a conditional write claims the send, so a redelivered or re-planned batch never texts anyone
twice. Sends run on a few threads (HTTP gateways are slow). A transient failure leaves the recipient
FAILED and fails the message, so SQS retries just those recipients (up to the queue's maxReceiveCount,
then the dead-letter queue and its alarm); a gateway rejection (4xx) is permanent.

Environment: DELIVERIES_TABLE, INBOX_TABLE, ALERTS_TABLE, CELL, SMS_PROVIDER, SMS_SECRET_ARN, plus SMS_HTTP_*
(see common/sms.py).
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.exceptions import ClientError

from common import sms
from common.observe import log, metric

PROVIDER = os.environ.get("SMS_PROVIDER", "simulator")
THREADS = 8
STALE_S = 180  # a claim older than this (longer than the function timeout) belongs to a dead invocation
KEEP_S = 30 * 86400
INBOX_KEEP_S = 2 * 86400
secrets = boto3.client("secretsmanager")
_local = threading.local()
_secret_cache = {}
# Module-level, so its threads (and their per-thread DynamoDB resources) are reused by warm invocations.
POOL = ThreadPoolExecutor(max_workers=THREADS)


def tables():
    """boto3 resources are not thread-safe, so each sending thread gets its own."""
    if not hasattr(_local, "ddb"):
        _local.ddb = boto3.session.Session().resource("dynamodb")
        _local.deliveries = _local.ddb.Table(os.environ["DELIVERIES_TABLE"])
        _local.inbox = _local.ddb.Table(os.environ["INBOX_TABLE"])
        _local.alerts = _local.ddb.Table(os.environ["ALERTS_TABLE"])
    return _local


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            if not deliver_batch(json.loads(record["body"])):
                failures.append({"itemIdentifier": record["messageId"]})
        except Exception as exc:
            log("send_failed", message_id=record["messageId"], error=str(exc))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def deliver_batch(message):
    """Returns False when a recipient failed transiently, so the batch is retried for them."""
    alert, recipients = message["alert"], message["recipients"]
    results = list(POOL.map(lambda sub: deliver_one(alert, sub), recipients))
    sent = results.count("SENT")
    if sent:
        count_sent(alert, sent)
    log("batch_sent", alert_id=alert["alert_id"], recipients=len(recipients), sent=sent,
        skipped=results.count("SKIPPED"), failed=results.count("FAILED"), rejected=results.count("REJECTED"))
    return "FAILED" not in results


def deliver_one(alert, sub):
    t, now = tables(), time.time()
    channel = "sms" if PROVIDER == "http" and sub.get("msisdn") else "sim"
    key = {"alert_id": alert["alert_id"], "subscriber_id": sub["subscriber_id"]}
    try:  # claim: first time, a transient failure being retried, or a claim abandoned by a dead invocation
        t.deliveries.put_item(
            Item={**key, "status": "SENDING", "channel": channel, "lang": sub.get("lang", "en"), "tier": alert["tier"],
                  "sent_at": int(now), "expires_at": int(now) + KEEP_S},
            ConditionExpression="attribute_not_exists(subscriber_id) OR #s = :failed OR (#s = :sending AND sent_at < :stale)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":failed": "FAILED", ":sending": "SENDING", ":stale": int(now) - STALE_S},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "SKIPPED"  # already sent, delivered, rejected, or being sent right now
        raise

    text = alert["texts"].get(sub.get("lang", "en"), alert["texts"]["en"])
    if channel == "sms":
        result = sms.send_http(sub["msisdn"], text, f"{alert['alert_id']}|{sub['subscriber_id']}", load_secret())
    else:
        result = {"status": "SENT"}
    status = result["status"]

    if status == "SENT":
        inbox_sk = f"{int(now * 1000)}#{alert['alert_id']}"
        t.inbox.put_item(Item={
            "subscriber_id": sub["subscriber_id"], "sk": inbox_sk, "alert_id": alert["alert_id"], "tier": alert["tier"],
            "text": text, "lang": sub.get("lang", "en"), "channel": channel, "status": alert.get("status", "Actual"),
            "trigger_rx_ms": alert["trigger_rx_ms"], "expires_at": int(now) + INBOX_KEEP_S,
        })
        t.deliveries.update_item(
            Key=key, UpdateExpression="SET #s = :s, inbox_sk = :k, provider_id = :p",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "SENT", ":k": inbox_sk, ":p": result.get("provider_id") or "-"},
        )
    else:
        t.deliveries.update_item(
            Key=key, UpdateExpression="SET #s = :s, #e = :e", ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":s": status, ":e": result.get("error") or "-"},
        )
    metric("Sent", 1, Channel=channel, Tier=alert["tier"], Result=status)
    return status


def count_sent(alert, n):
    item = tables().alerts.update_item(
        Key={"alert_id": alert["alert_id"], "sk": "META"}, UpdateExpression="ADD sent :n",
        ExpressionAttributeValues={":n": n}, ReturnValues="ALL_NEW",
    )["Attributes"]
    audience, total = item.get("audience"), int(item.get("sent", 0))
    # The sender whose batch completes the audience reports how long the whole fan-out took.
    if audience is not None and total - n < int(audience) <= total:
        metric("DrainSeconds", time.time() - int(item.get("planned_at", time.time())), "Seconds", Tier=alert["tier"])


def load_secret():
    if "v" not in _secret_cache:
        _secret_cache["v"] = json.loads(secrets.get_secret_value(SecretId=os.environ["SMS_SECRET_ARN"])["SecretString"])
    return _secret_cache["v"]
