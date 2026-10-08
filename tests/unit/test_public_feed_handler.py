import json
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime


def test_writes_real_zones_only_with_a_short_cache(stack):
    for zid, tier in (("Z07", "warning"), ("Z08", "normal"), ("T01", "imminent"), ("H00", "imminent"), ("L01", "watch")):
        stack.table("zone-state").put_item(Item={"zone_id": zid, "tier": tier, "blind": zid == "Z08", "updated_at": 100})
    feed = stack.load("public_feed.handler")
    assert feed.handler({}, None) == {"zones": 2, "alerts": 0}
    obj = stack.s3.get_object(Bucket="t-web", Key="public/zones.json")
    assert obj["CacheControl"] == "public, max-age=5" and obj["ContentType"] == "application/json"
    body = json.loads(obj["Body"].read())
    assert body["zones"] == [
        {"zone_id": "Z07", "tier": "warning", "blind": False, "updated_at": 100},
        {"zone_id": "Z08", "tier": "normal", "blind": True, "updated_at": 100},
    ]
    assert body["generated_at"] > 0


def put_alert(stack, alert_id, zone_id, status, tier="imminent", age_s=60):
    issued = int(time.time()) - age_s
    stack.table("alerts").put_item(Item={
        "alert_id": alert_id, "sk": "META", "day": datetime.fromtimestamp(issued, UTC).strftime("%Y%m%d"),
        "issued_at": issued, "zone_id": zone_id, "zone_name": f"Zone {zone_id}", "district": "Ratnapura", "tier": tier,
        "hazard": "flood", "status": status, "cap_key": f"cap/x/{alert_id}.xml", "texts": {"en": f"text {alert_id}"},
    })


def test_cap_feed_lists_recent_real_alerts_newest_first_linking_to_cap_files(stack):
    put_alert(stack, "A-old", "Z07", "Actual", age_s=600)
    put_alert(stack, "A-new", "Z08", "Exercise", tier="clear", age_s=60)
    put_alert(stack, "A-test", "Z07", "Test")
    put_alert(stack, "A-hb", "H00", "Actual")
    put_alert(stack, "A-stale", "Z07", "Actual", age_s=3 * 86400)
    feed = stack.load("public_feed.handler")
    assert feed.handler({}, None)["alerts"] == 2
    obj = stack.s3.get_object(Bucket="t-web", Key="public/cap.atom")
    assert obj["ContentType"] == "application/atom+xml"
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(obj["Body"].read())
    entries = root.findall("a:entry", ns)
    assert [e.find("a:id", ns).text for e in entries] == ["urn:sentinelmesh:alert:A-new", "urn:sentinelmesh:alert:A-old"]
    assert entries[0].find("a:title", ns).text == "ALL CLEAR (Exercise) - Zone Z08, Ratnapura (flood)"
    link = entries[1].find("a:link", ns)
    assert (link.get("type"), link.get("href")) == ("application/cap+xml", "/cap/x/A-old.xml")
    assert root.find("a:updated", ns).text == entries[0].find("a:updated", ns).text
