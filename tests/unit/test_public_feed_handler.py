import json


def test_writes_real_zones_only_with_a_short_cache(stack):
    for zid, tier in (("Z07", "warning"), ("Z08", "normal"), ("T01", "imminent"), ("H00", "imminent"), ("L01", "watch")):
        stack.table("zone-state").put_item(Item={"zone_id": zid, "tier": tier, "blind": zid == "Z08", "updated_at": 100})
    feed = stack.load("public_feed.handler")
    assert feed.handler({}, None) == {"zones": 2}
    obj = stack.s3.get_object(Bucket="t-web", Key="public/zones.json")
    assert obj["CacheControl"] == "public, max-age=5" and obj["ContentType"] == "application/json"
    body = json.loads(obj["Body"].read())
    assert body["zones"] == [
        {"zone_id": "Z07", "tier": "warning", "blind": False, "updated_at": 100},
        {"zone_id": "Z08", "tier": "normal", "blind": True, "updated_at": 100},
    ]
    assert body["generated_at"] > 0
