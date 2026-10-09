"""Public API (no login; WAF rate limits in front):

POST   /reports              citizen report -> IoT topic (phone kept only as a salted hash)
GET    /public/zones         zone tiers for the public map (no evidence, no people data)
POST   /phone/verify/start   text a 6-digit code to a number before it may receive alerts by SMS
POST   /phone/verify/finish  check the code -> short-lived verification token for that number
POST   /phone/subscribe      handset opts in: zone, language, ~1 km geohash cell, and optionally a verified number
DELETE /phone/subscribe      handset withdraws consent: its record is erased (X-Device-Token required)
GET    /phone/inbox          handset's messages (X-Device-Token required)
POST   /phone/ack            handset confirms receipt -> delivery receipt + time-to-first-alert
POST   /sms/dlr              third-party SMS delivery report (shared secret)

Phone numbers are stored only after the owner proves they hold the phone (the code), only for subscribers
who asked for SMS, with the consent version and time, and are deleted on unsubscribe.

Environment: ZONES_TABLE, STATE_TABLE, SUBSCRIBERS_TABLE, INBOX_TABLE, ALERTS_TABLE, RATELIMITS_TABLE,
DELIVERIES_TABLE, IOT_ENDPOINT, REPORT_PEPPER_SECRET_ARN, SMS_SECRET_ARN, SMS_PROVIDER (+ the provider's
settings, see common/sms.py), REAL_SMS_ALLOWLIST (optional: comma-separated numbers allowed real SMS).
"""

import hashlib
import hmac
import json
import os
import re
import secrets as rand
import time
import uuid

import boto3
from boto3.dynamodb.conditions import Key

from common import receipts, sms
from common.api import respond
from common.observe import log, metric

ddb = boto3.resource("dynamodb")
iot = boto3.client("iot-data", endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}")
secrets = boto3.client("secretsmanager")
_pepper = {}
zones = ddb.Table(os.environ["ZONES_TABLE"])
states = ddb.Table(os.environ["STATE_TABLE"])
subscribers = ddb.Table(os.environ["SUBSCRIBERS_TABLE"])
inbox = ddb.Table(os.environ["INBOX_TABLE"])
alerts = ddb.Table(os.environ["ALERTS_TABLE"])
limits = ddb.Table(os.environ["RATELIMITS_TABLE"])
deliveries = ddb.Table(os.environ["DELIVERIES_TABLE"])

ZONE_RE = re.compile(r"^[ZTH]\d{2}$")
GEOHASH_RE = re.compile(r"^[0-9b-hjkmnp-z]{6}$")
REPORT_KINDS = {"flooding", "water_rising", "landslide", "crack"}
LANGS = {"en", "si", "ta"}
REPORTS_PER_10_MIN = 3
CONSENT_VERSION = "2026-10"  # what the phone page tells people they agree to
OTP_TTL_S, OTP_PER_HOUR, OTP_ATTEMPTS, VERIFIED_TTL_S = 600, 3, 5, 600
SL_MOBILE = re.compile(r"^\+947\d{8}$")
PROVIDER = os.environ.get("SMS_PROVIDER", "simulator")
_eum = {}


def handler(event, context):
    route = (event.get("httpMethod"), event.get("resource"))
    body = json.loads(event.get("body") or "{}") if event.get("body") else {}
    try:
        if route == ("POST", "/reports"):
            return report(body)
        if route == ("GET", "/public/zones"):
            return public_zones()
        if route == ("POST", "/phone/verify/start"):
            return verify_start(body)
        if route == ("POST", "/phone/verify/finish"):
            return verify_finish(body)
        if route == ("POST", "/phone/subscribe"):
            return subscribe(body)
        if route == ("DELETE", "/phone/subscribe"):
            return unsubscribe(event)
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


def pepper():
    """The reporter-hash key, from Secrets Manager (not a plain environment variable anyone reading the
    function's configuration could see). Cached for the life of the container."""
    if "v" not in _pepper:
        _pepper["v"] = secrets.get_secret_value(SecretId=os.environ["REPORT_PEPPER_SECRET_ARN"])["SecretString"]
    return _pepper["v"]


def reporter_hash(phone):
    return hmac.new(pepper().encode(), sms.normalise_msisdn(phone).encode(), hashlib.sha256).hexdigest()[:24]


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
    item = {
        "subscriber_id": f"sim-{uuid.uuid4().hex[:12]}", "zone_id": zone_id, "lang": lang, "geohash6": geohash6,
        "device_token_sha256": hashlib.sha256(token.encode()).hexdigest(), "consent": CONSENT_VERSION,
        "consent_at": int(time.time()), "created_at": int(time.time()),
    }
    if body.get("msisdn"):  # SMS too: only for a number its owner has just verified
        msisdn = sms.normalise_msisdn(body["msisdn"])
        require(verified(msisdn, body.get("verify_token", "")), "verify the phone number first")
        item |= {"msisdn": msisdn, "channels": "sms"}
    subscribers.put_item(Item=item)
    log("subscribed", subscriber_id=item["subscriber_id"], zone_id=zone_id, lang=lang, sms=bool(item.get("msisdn")))
    return respond(201, {"subscriber_id": item["subscriber_id"], "sms": bool(item.get("msisdn"))})


def unsubscribe(event):
    """Withdraw consent: the subscriber record (and with it any phone number) is erased."""
    sub = _owner(event)
    if not sub:
        return respond(403, {"error": "unknown handset"})
    subscribers.delete_item(Key={"subscriber_id": sub["subscriber_id"]})
    log("unsubscribed", subscriber_id=sub["subscriber_id"])
    return respond(200, {"unsubscribed": True})


def _otp_key(msisdn):
    return hmac.new(pepper().encode(), msisdn.encode(), hashlib.sha256).hexdigest()[:24]


def _code_hash(msisdn, code):
    return hmac.new(pepper().encode(), f"{msisdn}|{code}".encode(), hashlib.sha256).hexdigest()


def verified(msisdn, token):
    """A token from verify_finish: '<expiry>.<hmac(number, expiry)>'."""
    expiry, _, mac = str(token).partition(".")
    if not expiry.isdigit() or int(expiry) < time.time():
        return False
    expected = hmac.new(pepper().encode(), f"verified|{msisdn}|{expiry}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, expected)


def allowed(msisdn):
    allow = {sms.normalise_msisdn(n) for n in os.environ.get("REAL_SMS_ALLOWLIST", "").split(",") if n.strip()}
    return not allow or msisdn in allow


def send_code(msisdn, code):
    text = f"SentinelMesh code {code}. It expires in 10 minutes. Ignore this if you did not ask for it."
    if PROVIDER == "eum":
        if "c" not in _eum:
            _eum["c"] = boto3.client("pinpoint-sms-voice-v2")
        return sms.send_eum(msisdn, text, "verify", _eum["c"], os.environ.get("EUM_ORIGINATION_IDENTITY"),
                            os.environ.get("EUM_CONFIGURATION_SET"))
    if PROVIDER == "http":
        secret = json.loads(secrets.get_secret_value(SecretId=os.environ["SMS_SECRET_ARN"])["SecretString"])
        return sms.send_http(msisdn, text, "verify", secret)
    return {"status": "SIMULATED"}


def verify_start(body):
    msisdn = sms.normalise_msisdn(body.get("msisdn", ""))
    require(SL_MOBILE.match(msisdn), "a Sri Lankan mobile number is needed, e.g. 0771234567")
    if not allowed(msisdn):
        return respond(403, {"error": "real SMS is limited to the demo phones"})
    key = _otp_key(msisdn)
    count = limits.update_item(
        Key={"k": f"otp-rate#{key}#{int(time.time() // 3600)}"}, UpdateExpression="ADD n :one SET expires_at = :exp",
        ExpressionAttributeValues={":one": 1, ":exp": int(time.time()) + 7200}, ReturnValues="UPDATED_NEW",
    )["Attributes"]["n"]
    if count > OTP_PER_HOUR:
        return respond(429, {"error": "too many codes for this number; try again later"})
    code = f"{rand.randbelow(10**6):06d}"
    limits.put_item(Item={"k": f"otp#{key}", "code": _code_hash(msisdn, code), "attempts": 0,
                          "expires_at": int(time.time()) + OTP_TTL_S})
    result = send_code(msisdn, code)
    if result["status"] == "SIMULATED":
        # No SMS provider in this stack: hand the code back, clearly labelled, so the demo phone can continue.
        return respond(202, {"sent": False, "simulator_code": code})
    if result["status"] != "SENT":
        return respond(502, {"error": "the code could not be sent; try again"})
    return respond(202, {"sent": True})


def verify_finish(body):
    msisdn = sms.normalise_msisdn(body.get("msisdn", ""))
    code = str(body.get("code", ""))
    item = limits.get_item(Key={"k": f"otp#{_otp_key(msisdn)}"}, ConsistentRead=True).get("Item")
    if not item or int(item["expires_at"]) < time.time() or int(item["attempts"]) >= OTP_ATTEMPTS:
        return respond(400, {"error": "no valid code for this number; ask for a new one"})
    if not hmac.compare_digest(item["code"], _code_hash(msisdn, code)):
        limits.update_item(Key={"k": f"otp#{_otp_key(msisdn)}"}, UpdateExpression="ADD attempts :one",
                           ExpressionAttributeValues={":one": 1})
        return respond(400, {"error": "wrong code"})
    limits.delete_item(Key={"k": f"otp#{_otp_key(msisdn)}"})
    expiry = int(time.time()) + VERIFIED_TTL_S
    mac = hmac.new(pepper().encode(), f"verified|{msisdn}|{expiry}".encode(), hashlib.sha256).hexdigest()
    return respond(200, {"verify_token": f"{expiry}.{mac}"})


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
    out = receipts.record(inbox, deliveries, alerts, subscriber_id, sk, channel)
    return respond(200, out)


def dlr(event, body):
    params = {**(event.get("queryStringParameters") or {}), **body}
    expected = json.loads(secrets.get_secret_value(SecretId=os.environ["SMS_SECRET_ARN"])["SecretString"]).get("dlr_secret")
    provided = params.pop("k", None) or {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get("x-dlr-secret")
    if not sms.dlr_secret_ok(provided, expected):
        return respond(403, {"error": "bad secret"})
    ref, status = sms.parse_dlr(params)
    if not ref or "|" not in ref:
        return respond(400, {"error": "missing ref"})
    metric("SmsDeliveryReports", 1, Status=status)
    if status == "FAILED":
        receipts.undelivered(deliveries, ref, status)
    if status != "DELIVERED":
        return respond(200, {"status": status})
    out = receipts.record_for_ref(inbox, deliveries, alerts, ref, "sms")
    return respond(200, out if "acknowledged" in out or "duplicate" in out else {"status": status})
