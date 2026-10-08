import json

from common.districts import slug


def test_slug():
    assert slug("Nuwara Eliya") == "nuwara-eliya" and slug("Ratnapura") == "ratnapura"


def test_one_private_bundle_per_district_with_zones_and_recipients(stack):
    subs = stack.table("subscribers")
    subs.put_item(Item={"subscriber_id": "s1", "zone_id": "Z07", "lang": "si", "geohash6": "x"})
    subs.put_item(Item={"subscriber_id": "s2", "zone_id": "Z07", "lang": "en", "geohash6": "x", "msisdn": "+94770000001"})
    subs.put_item(Item={"subscriber_id": "t1", "zone_id": "T01", "lang": "en", "geohash6": "x"})
    mod = stack.load("edge_bundle.handler")
    out = mod.handler({}, None)["bundles"]
    assert set(out) == {"Ratnapura", "Kalutara", "Kegalle", "Colombo", "Gampaha", "Nuwara Eliya"}  # no Test/Load district
    assert out["Nuwara Eliya"]["key"] == "edge/sentinelmesh-node-nuwara-eliya/bundle.json"
    bundle = json.loads(stack.body("EVIDENCE_BUCKET", out["Ratnapura"]["key"]))
    assert bundle["district"] == "Ratnapura" and len(bundle["zones"]) == 18
    z07 = next(z for z in bundle["zones"] if z["zone_id"] == "Z07")
    assert z07["danger_level_m"] > z07["warning_level_m"] and len(z07["polygon"]) == 7
    assert sorted(bundle["recipients"], key=lambda r: r["subscriber_id"]) == [
        {"subscriber_id": "s1", "zone_id": "Z07", "lang": "si"},
        {"subscriber_id": "s2", "zone_id": "Z07", "lang": "en", "msisdn": "+94770000001"},
    ]


def test_version_changes_only_with_content(stack):
    mod = stack.load("edge_bundle.handler")
    zones = [z for z in mod.scan_all(mod.zones) if z["district"] == "Kegalle"]
    a, b = mod.build("Kegalle", zones, 100), mod.build("Kegalle", zones, 200)
    assert a["version"] == b["version"] and a["generated_at"] != b["generated_at"]
    stack.table("subscribers").put_item(Item={"subscriber_id": "s9", "zone_id": zones[0]["zone_id"], "lang": "ta", "geohash6": "x"})
    assert mod.build("Kegalle", zones, 300)["version"] != a["version"]
