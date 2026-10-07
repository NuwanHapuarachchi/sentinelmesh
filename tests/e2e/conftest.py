"""End-to-end fixtures. These tests talk to the deployed stack, using only test zones (T01-T04),
which are hidden from the dashboard and have 20-second timers."""

import json
import time
import uuid
from pathlib import Path

import boto3
import pytest

REGION = "ap-southeast-2"
PREFIX = "sentinelmesh"
OUTPUTS = Path(__file__).resolve().parents[2] / "outputs.json"


@pytest.fixture(scope="session")
def aws():
    s = boto3.session.Session(region_name=REGION)
    endpoint = s.client("iot").describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    return {
        "s": s,
        "ddb": s.resource("dynamodb"),
        "iotdata": s.client("iot-data", endpoint_url=f"https://{endpoint}"),
        "lambda": s.client("lambda"),
        "s3": s.client("s3"),
        "sfn": s.client("stepfunctions"),
    }


@pytest.fixture(scope="session")
def outputs():
    if OUTPUTS.exists():
        return {k: v["value"] for k, v in json.loads(OUTPUTS.read_text()).items()}
    return {}


def table(aws, name):
    return aws["ddb"].Table(f"{PREFIX}-{name}")


@pytest.fixture
def publish(aws):
    def _publish(zone_id, source_class, **values):
        payload = {"source_class": source_class, "source_id": f"e2e-{source_class}", "ts": time.time(), "drill": True, **values}
        aws["iotdata"].publish(topic=f"sm/obs/{zone_id}", qos=1, payload=json.dumps(payload))

    return _publish


def wait_for(check, timeout=90, every=3):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = check()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"condition not met within {timeout}s (last={last!r})")


@pytest.fixture
def reset_zone(aws):
    """Clears a test zone's state, alerts and subscribers before and after the test."""
    touched = []

    def _reset(zone_id, owner=None):
        touched.append((zone_id, owner))
        table(aws, "zone-state").delete_item(Key={"zone_id": zone_id})
        if owner:
            table(aws, "zones").update_item(
                Key={"zone_id": zone_id}, UpdateExpression="SET owner_cell = :c", ExpressionAttributeValues={":c": owner}
            )

    yield _reset
    for zone_id, owner in touched:
        table(aws, "zone-state").delete_item(Key={"zone_id": zone_id})
        if owner:
            table(aws, "zones").update_item(
                Key={"zone_id": zone_id}, UpdateExpression="SET owner_cell = :c", ExpressionAttributeValues={":c": owner}
            )


@pytest.fixture
def subscriber(aws):
    created = []

    def _subscribe(zone_id, lang="en"):
        sid = f"e2e-{uuid.uuid4().hex[:10]}"
        table(aws, "subscribers").put_item(
            Item={"subscriber_id": sid, "zone_id": zone_id, "lang": lang, "geohash6": "tc1xyz", "consent": "e2e"}
        )
        created.append(sid)
        return sid

    yield _subscribe
    for sid in created:
        table(aws, "subscribers").delete_item(Key={"subscriber_id": sid})


def alerts_for(aws, zone_id, since):
    """META alert items for a zone issued after `since` (scan is fine at test scale)."""
    items = table(aws, "alerts").scan(
        FilterExpression="zone_id = :z AND sk = :m AND issued_at >= :t",
        ExpressionAttributeValues={":z": zone_id, ":m": "META", ":t": int(since) - 1},
    )["Items"]
    return sorted(items, key=lambda i: i["issued_at"])
