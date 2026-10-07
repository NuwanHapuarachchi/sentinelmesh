"""Merge observations into a zone's stored state and decide the tier. No AWS calls here.

The stored state keeps scalar attributes (tier, score, blind, version) for queries and dashboards,
and a JSON "doc" for the evidence, so the scorer never fights DynamoDB's Decimal types.
"""

from common import scoring as sc

VALUE_FIELD = {"gauge": "level_m", "rain": "rain_24h_mm", "tilt": "tilt_deg", "forecast": "fc_rain_24h_mm"}
GAUGE_HISTORY = 6
REPORT_KEEP_S = 7200
SCORE_HISTORY_S = 1800


def empty_doc():
    return {
        "ev": {},
        "last_seen": {},
        "gauge_hist": [],
        "reports": [],
        "score_hist": [],
        "pending_down_since": None,
        "episode": 0,
    }


def merge(doc, observations, now):
    """Fold observations (dicts from the observations table) into doc, ignoring out-of-order readings."""
    for o in sorted(observations, key=lambda x: float(x.get("ts") or x.get("rx_ms", 0) / 1000)):
        cls = o.get("source_class")
        ts = float(o.get("ts") or o.get("rx_ms", now * 1000) / 1000)
        if cls == "report":
            if o.get("reporter_hash") and o.get("report_kind"):
                doc["reports"].append(
                    {"h": o["reporter_hash"], "sev": int(o.get("report_sev", 1)), "kind": o["report_kind"], "ts": ts}
                )
            continue
        field = VALUE_FIELD.get(cls)
        if field is None or o.get(field) is None:
            continue
        value = float(o[field])
        current = doc["ev"].get(cls)
        if current is None or ts >= current["ts"]:
            doc["ev"][cls] = {"value": value, "ts": ts, "src": o.get("source_id")}
        doc["last_seen"][cls] = max(ts, doc["last_seen"].get(cls, 0))
        if cls == "gauge":
            doc["gauge_hist"] = sorted([*doc["gauge_hist"], [ts, value]])[-GAUGE_HISTORY:]
    doc["reports"] = [r for r in doc["reports"] if now - r["ts"] <= REPORT_KEEP_S][-20:]
    return doc


def evaluate(zone, doc, previous_tier, now):
    """Returns (assessment, new_tier, tier_changed_up)."""
    a = sc.assess(
        zone,
        doc["ev"],
        doc["reports"],
        [tuple(x) for x in doc["gauge_hist"]],
        doc["last_seen"],
        {"tier": previous_tier, "score_hist": [tuple(x) for x in doc["score_hist"]]},
        now,
    )
    hold_down = int(zone.get("hold_down_s") or (20 if zone.get("is_test") else 600))
    tier, pending = sc.next_tier(previous_tier, a.tier, a.score, a.agreeing, doc["pending_down_since"], now, hold_down)
    doc["pending_down_since"] = pending
    doc["score_hist"] = [x for x in doc["score_hist"] if now - x[0] <= SCORE_HISTORY_S][-30:] + [[now, a.score]]

    went_up = sc.RANK[tier] > sc.RANK[previous_tier]
    # A new alert episode starts when the zone climbs from below advisory to advisory or above.
    if went_up and sc.RANK[tier] >= sc.RANK["advisory"] and sc.RANK[previous_tier] < sc.RANK["advisory"]:
        doc["episode"] += 1
        doc["episode_started"] = int(now)
    return a, tier, went_up
