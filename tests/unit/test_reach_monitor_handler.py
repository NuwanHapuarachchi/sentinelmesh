import time
from datetime import UTC, datetime

import pytest


@pytest.fixture
def reach(stack):
    mod = stack.load("reach_monitor.handler")
    mod.metrics = []
    mod.metric = lambda name, value, unit="Count", **dims: mod.metrics.append((name, value, dims))
    return mod


def put_alert(stack, alert_id, zone_id="Z07", age_s=600, audience=4, sent=4, status="Actual", tier="imminent"):
    issued = int(time.time()) - age_s
    item = {"alert_id": alert_id, "sk": "META", "day": datetime.fromtimestamp(issued, UTC).strftime("%Y%m%d"),
            "issued_at": issued, "zone_id": zone_id, "tier": tier, "status": status, "sent": sent}
    if audience is not None:
        item["audience"] = audience
    stack.table("alerts").put_item(Item=item)


def put_ledger(stack, alert_id, rows):
    for i, (channel, status) in enumerate(rows):
        stack.table("deliveries").put_item(Item={"alert_id": alert_id, "subscriber_id": f"s{i}", "channel": channel, "status": status})


def values(reach, name):
    return [(v, d) for n, v, d in reach.metrics if n == name]


def test_delivery_rate_per_channel_once_receipts_have_had_time(stack, reach):
    put_alert(stack, "A1", audience=6, sent=5)
    put_ledger(stack, "A1", [("sms", "DELIVERED"), ("sms", "DELIVERED"), ("sms", "DELIVERED"), ("sms", "SENT"),
                             ("sim", "SENT"), ("sms", "SENDING")])
    out = reach.handler({}, None)
    assert out["alerts"] == 1
    assert (75.0, {"Tier": "imminent", "Channel": "sms"}) in values(reach, "DeliveryRatePct")
    assert (0.0, {"Tier": "imminent", "Channel": "sim"}) in values(reach, "DeliveryRatePct")
    assert (1, {"Tier": "imminent", "Channel": "sms"}) in values(reach, "Undelivered")
    assert values(reach, "Unsent") == [(1, {"Tier": "imminent"})]


def test_a_fresh_alert_reports_progress_but_no_rate_yet(stack, reach):
    put_alert(stack, "A1", age_s=30)
    put_ledger(stack, "A1", [("sms", "SENT")] * 2)
    reach.handler({}, None)
    assert values(reach, "DeliveryRatePct") == []
    assert values(reach, "Undelivered") == [(2, {"Tier": "imminent", "Channel": "sms"})]


def test_test_alerts_unplanned_alerts_and_old_alerts_are_ignored(stack, reach):
    put_alert(stack, "hb", zone_id="H00")
    put_alert(stack, "drill-test", status="Test")
    put_alert(stack, "unplanned", audience=None)
    put_alert(stack, "old", age_s=7200)
    out = reach.handler({}, None)
    assert out == {"alerts": 0, "results": []}
    assert reach.metrics == []
