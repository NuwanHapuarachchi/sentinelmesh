"""Public feed: writes the public files to the web bucket, served by CloudFront with a 5-second cache.

public/zones.json  every real zone's tier and blind flag, for the public map
public/cap.atom    CAP-over-Atom feed of the last 48 hours of alerts (newest first), each entry linking to its
                   CAP 1.2 file, so agencies and broadcast gateways can subscribe to one URL

Reading files instead of calling the API keeps the public map and feed working through an API or Lambda
outage (static stability), at the same cost however many people refresh them. Runs every minute, when a
scorer sees a tier change, and after the issuer issues an alert.

Environment: STATE_TABLE, ALERTS_TABLE, WEB_BUCKET.
"""

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.conditions import Key

from common.cap import cap_time

ddb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
states = ddb.Table(os.environ["STATE_TABLE"])
alerts = ddb.Table(os.environ["ALERTS_TABLE"])
TEST_ZONE_PREFIXES = ("T", "H", "L")
FEED_WINDOW_S = 48 * 3600
FEED_MAX = 100
ATOM = "http://www.w3.org/2005/Atom"


def handler(event, context):
    now = time.time()
    zones = public_zones()
    put("public/zones.json", json.dumps({"generated_at": int(now), "zones": zones}).encode("utf-8"), "application/json")
    entries = recent_alerts(now)
    put("public/cap.atom", cap_feed(entries, now), "application/atom+xml")
    return {"zones": len(zones), "alerts": len(entries)}


def put(key, body, content_type):
    s3.put_object(Bucket=os.environ["WEB_BUCKET"], Key=key, Body=body, ContentType=content_type,
                  CacheControl="public, max-age=5")


def hidden(zone_id):
    return str(zone_id).startswith(TEST_ZONE_PREFIXES)


def public_zones():
    zones, kwargs = [], {"ProjectionExpression": "zone_id, tier, blind, updated_at"}
    while True:
        page = states.scan(**kwargs)
        zones += [
            {"zone_id": z["zone_id"], "tier": z.get("tier", "normal"), "blind": bool(z.get("blind")),
             "updated_at": int(z["updated_at"]) if z.get("updated_at") is not None else None}
            for z in page["Items"] if not hidden(z["zone_id"])
        ]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return sorted(zones, key=lambda z: z["zone_id"])


def recent_alerts(now):
    """Non-test alerts for real zones issued in the last 48 hours, newest first."""
    since = int(now - FEED_WINDOW_S)
    days = sorted({datetime.fromtimestamp(now - s, UTC).strftime("%Y%m%d") for s in range(0, FEED_WINDOW_S + 1, 86400)})
    items = []
    for day in days:
        kwargs = {"IndexName": "by-day", "KeyConditionExpression": Key("day").eq(day) & Key("issued_at").gte(since)}
        while True:
            page = alerts.query(**kwargs)
            items += [a for a in page["Items"] if a.get("status") != "Test" and not hidden(a.get("zone_id"))]
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return sorted(items, key=lambda a: int(a["issued_at"]), reverse=True)[:FEED_MAX]


def cap_feed(items, now):
    ET.register_namespace("", ATOM)
    feed = ET.Element(f"{{{ATOM}}}feed")

    def sub(parent, tag, text=None, **attrs):
        el = ET.SubElement(parent, f"{{{ATOM}}}{tag}", attrs)
        if text is not None:
            el.text = str(text)
        return el

    sub(feed, "id", "urn:sentinelmesh:cap")
    sub(feed, "title", "SentinelMesh alerts (CAP 1.2)")
    sub(feed, "updated", cap_time(int(items[0]["issued_at"]) if items else now))
    sub(sub(feed, "author"), "name", "SentinelMesh Disaster Early Warning")
    sub(feed, "link", rel="self", href="/public/cap.atom")
    for a in items:
        entry = sub(feed, "entry")
        sub(entry, "id", f"urn:sentinelmesh:alert:{a['alert_id']}")
        tier = "ALL CLEAR" if a["tier"] == "clear" else a["tier"].upper()
        status = "" if a.get("status") == "Actual" else f" ({a.get('status')})"
        sub(entry, "title", f"{tier}{status} - {a.get('zone_name', a['zone_id'])}, {a.get('district', '')} ({a.get('hazard', '')})")
        sub(entry, "updated", cap_time(int(a["issued_at"])))
        sub(entry, "link", rel="alternate", type="application/cap+xml", href=f"/{a['cap_key']}")
        if (a.get("texts") or {}).get("en"):
            sub(entry, "summary", a["texts"]["en"])
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)
