"""Public API (no login; WAF rate limits in front):

POST /reports            citizen report -> IoT topic (phone kept only as a salted hash)
GET  /public/zones       zone tiers for the public map (no evidence, no people data)
POST /phone/subscribe    simulated handset opts in: zone, language, ~1 km geohash cell
GET  /phone/inbox        handset's messages (X-Device-Token required)
POST /phone/ack          handset confirms receipt -> delivery receipt + time-to-first-alert
POST /sms/dlr            third-party SMS delivery report (shared secret)

Environment: ZONES_TABLE, STATE_TABLE, SUBSCRIBERS_TABLE, INBOX_TABLE, ALERTS_TABLE,
RATELIMITS_TABLE, IOT_ENDPOINT, REPORT_PEPPER, SMS_SECRET_ARN.
"""

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common import sms
from common.api import respond
from common.observe import log, metric

ddb = boto3.resource("dynamodb")
iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}")
secrets = boto3.client("secretsmanager")
zones = ddb.Table(os.environ["ZONES_TABLE"])
states = ddb.Table(os.environ["STATE_TABLE"])
subscribers = ddb.Table(os.environ["SUBSCRIBERS_TABLE"])
inbox = ddb.Table(os.environ["INBOX_TABLE"])
alerts = ddb.Table(os.environ["ALERTS_TABLE"])
limits = ddb.Table(os.environ["RATELIMITS_TABLE"])

ZONE_RE = re.compile(r"^[ZTH]\d{2}$")
GEOHASH_RE = re.compile(r"^[0-9b-hjkmnp-z]{6}$")
REPORT_KINDS = {"flooding", "water_rising", "landslide", "crack"}
LANGS = {"en", "si", "ta"}
REPORTS_PER_10_MIN = 3


def handler(event, context):
    route = (event.get("httpMethod"), event.get("resource"))
    body = json.loads(event.get("body") or "{}") if event.get("body") else {}
    try:
        if route == ("POST", "/reports"):
            return report(body)
        if route == ("GET", "/public/zones"):
            return public_zones()
        if route == ("POST", "/phone/subscribe"):
            return subscribe(body)
        if route == ("GET", "/phone/inbox"):
            return phone_inbox(event)
        if route == ("POST", "/phone/ack"):
            return ack(event, body)
        if route == ("POST", "/sms/dlr"):
            return dlr(event, body)
    except ValueError as exc:
        return respond(400, {"error": str(exc)})
    return respond(404, {"error": "not found"})


def require(cond, message):
    if not cond:
        raise ValueError(message)


def reporter_hash(phone):
    return hmac.new(os.environ["REPORT_PEPPER"].encode(), sms.normalise_msisdn(phone).encode(), hashlib.sha256).hexdigest()[:24]


def report(body):
    zone_id, kind, sev, phone = body.get("zone_id", ""), body.get("kind"), body.get("severity", 1), body.get("phone", "")
    require(ZONE_RE.match(zone_id), "zone_id must look like Z01")
    require(kind in REPORT_KINDS, f"kind must be one of {sorted(REPORT_KINDS)}")
    require(isinstance(sev, int) and 1 <= sev <= 3, "severity must be 1, 2 or 3")
    require(len("".join(c for c in phone if c.isdigit())) >= 9, "a phone number is required (it is stored only as a hash)")
    require("lat" not in body and "lon" not in body, "send the zone, not coordinates")
    require(zones.get_item(Key={"zone_id": zone_id}).get("Item"), "unknown zone")

    h = reporter_hash(phone)
    window = int(time.time() // 600)
    count = limits.update_item(
        Key={"k": f"rep#{h}#{window}"}, UpdateExpression="ADD n :one SET expires_at = :exp",
        ExpressionAttributeValues={":one": 1, ":exp": int(time.time()) + 1200}, ReturnValues="UPDATED_NEW",
    )["Attributes"]["n"]
    if count > REPORTS_PER_10_MIN:
        return respond(429, {"error": "too many reports from this number; please wait"})

    iot.publish(topic=f"sm/obs/{zone_id}", qos=1, payload=json.dumps({
        "source_class": "report", "source_id": "report-api", "ts": time.time(),
        "reporter_hash": h, "report_kind": kind, "report_sev": sev, "drill": bool(body.get("drill")),
    }))
    metric("CitizenReports", 1, Kind=kind)
    return respond(202, {"accepted": True})


def public_zones():
    tiers = {i["zone_id"]: i for i in states.scan(ProjectionExpression="zone_id, tier, blind, updated_at")["Items"]}
    return respond(200, {"zones": [
        {"zone_id": z, "tier": s.get("tier", "normal"), "blind": bool(s.get("blind")), "updated_at": s.get("updated_at")}
        for z, s in tiers.items() if not z.startswith(("T", "H", "L"))
    ]})


def subscribe(body):
    zone_id, lang, geohash6 = body.get("zone_id", ""), body.get("lang", "en"), body.get("geohash6", "")
    require(ZONE_RE.match(zone_id) and zones.get_item(Key={"zone_id": zone_id}).get("Item"), "unknown zone")
    require(lang in LANGS, "lang must be en, si or ta")
    require(GEOHASH_RE.match(geohash6), "geohash6 must be a 6-character geohash (about 1 km); never send coordinates")
    token = body.get("device_token", "")
    require(len(token) >= 16, "device_token required")
    sid = f"sim-{uuid.uuid4().hex[:12]}"
    subscribers.put_item(Item={
        "subscriber_id": sid, "zone_id": zone_id, "lang": lang, "geohash6": geohash6,
        "device_token_sha256": hashlib.sha256(token.encode()).hexdigest(), "consent": "demo-opt-in",
        "created_at": int(time.time()),
    })
    log("subscribed", subscriber_id=sid, zone_id=zone_id, lang=lang)
    return respond(201, {"subscriber_id": sid})


def _owner(event):
    sid = (event.get("queryStringParameters") or {}).get("subscriber_id", "")
    token = {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get("x-device-token", "")
    sub = subscribers.get_item(Key={"subscriber_id": sid}).get("Item") if sid else None
    ok = sub and hmac.compare_digest(sub["device_token_sha256"], hashlib.sha256(token.encode()).hexdigest())
    return sub if ok else None


def phone_inbox(event):
    sub = _owner(event)
    if not sub:
        return respond(403, {"error": "unknown handset"})
    items = inbox.query(KeyConditionExpression=Key("subscriber_id").eq(sub["subscriber_id"]), ScanIndexForward=False, Limit=20)["Items"]
    return respond(200, {"zone_id": sub["zone_id"], "messages": items})


def ack(event, body):
    sub = _owner(event)
    if not sub:
        return respond(403, {"error": "unknown handset"})
    return receipt(sub["subscriber_id"], body.get("sk", ""), "sim")


def receipt(subscriber_id, sk, channel):
    now = time.time()
    try:
        item = inbox.update_item(
            Key={"subscriber_id": subscriber_id, "sk": sk}, UpdateExpression="SET acked_at = :t",
            ConditionExpression="attribute_exists(sk) AND attribute_not_exists(acked_at)",
            ExpressionAttributeValues={":t": int(now * 1000)}, ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return respond(200, {"duplicate": True})
        raise
    ttfa_ms = now * 1000 - int(item["trigger_rx_ms"])
    first = alerts.update_item(
        Key={"alert_id": item["alert_id"], "sk": "META"},
        UpdateExpression="ADD delivered :one SET first_receipt_ms = if_not_exists(first_receipt_ms, :ms)",
        ExpressionAttributeValues={":one": 1, ":ms": Decimal(int(ttfa_ms))}, ReturnValues="ALL_NEW",
    )["Attributes"]
    metric("DeliveryReceipts", 1, Channel=channel, Tier=item["tier"])
    if int(first.get("delivered", 0)) == 1:  # first confirmed delivery for this alert
        metric("TimeToFirstAlertMs", ttfa_ms, "Milliseconds", Tier=item["tier"])
    return respond(200, {"acknowledged": True, "ttfa_ms": int(ttfa_ms)})


def dlr(event, body):
    params = {**(event.get("queryStringParameters") or {}), **body}
    expected = json.loads(secrets.get_secret_value(SecretId=os.environ["SMS_SECRET_ARN"])["SecretString"]).get("dlr_secret")
    provided = params.pop("k", None) or {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get("x-dlr-secret")
    if not sms.dlr_secret_ok(provided, expected):
        return respond(403, {"error": "bad secret"})
    ref, status = sms.parse_dlr(params)
    if not ref or "|" not in ref:
        return respond(400, {"error": "missing ref"})
    alert_id, subscriber_id = ref.split("|", 1)
    metric("SmsDeliveryReports", 1, Status=status)
    if status != "DELIVERED":
        return respond(200, {"status": status})
    msgs = inbox.query(KeyConditionExpression=Key("subscriber_id").eq(subscriber_id))["Items"]
    match = next((m for m in msgs if m["alert_id"] == alert_id), None)
    return receipt(subscriber_id, match["sk"], "sms") if match else respond(200, {"status": status})
