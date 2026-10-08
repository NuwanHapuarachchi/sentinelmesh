"""Shared fixtures for handler tests.

`stack` starts an in-memory AWS (moto) holding the tables, buckets, queues, topic and secret the
handlers expect, with the same keys and indexes as the Terraform definitions. Handlers read their
environment and create clients at import time, so `stack.load("issuer.handler")` imports a fresh copy
after everything exists.
"""

import importlib
import json
import sys
from decimal import Decimal
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

REGION = "ap-southeast-2"
ZONES = json.loads((Path(__file__).resolve().parents[2] / "terraform" / "app" / "data" / "zones.json").read_text())

# name: (hash key, range key, {index: (hash key, range key)}); every key here is a string except the
# index range keys named in NUMERIC.
TABLES = {
    "zones": ("zone_id", None, {}),
    "zone-state": ("zone_id", None, {}),
    "alerts": ("alert_id", "sk", {"by-day": ("day", "issued_at")}),
    "approvals": ("approval_id", None, {"pending": ("pending", "created_at")}),
    "subscribers": ("subscriber_id", None, {"by-zone": ("zone_id", None)}),
    "inbox": ("subscriber_id", "sk", {}),
    "ratelimits": ("k", None, {}),
    "deliveries": ("alert_id", "subscriber_id", {}),
    "a-observations": ("zone_id", "sk", {}),
}
NUMERIC = {"issued_at", "created_at"}
BUCKETS = {"WEB_BUCKET": "t-web", "DECISIONS_BUCKET": "t-decisions", "EVIDENCE_BUCKET": "t-evidence"}
TIERS = ("imminent", "warning", "advisory")


def _create_table(ddb, name, hash_key, range_key, indexes):
    keys = [(hash_key, "HASH")] + ([(range_key, "RANGE")] if range_key else [])
    attrs = {hash_key, range_key} - {None}
    gsis = []
    for index, (ih, ir) in indexes.items():
        attrs |= {ih, ir} - {None}
        schema = [{"AttributeName": ih, "KeyType": "HASH"}] + ([{"AttributeName": ir, "KeyType": "RANGE"}] if ir else [])
        gsis.append({"IndexName": index, "KeySchema": schema, "Projection": {"ProjectionType": "ALL"}})
    kwargs = {
        "TableName": f"t-{name}",
        "BillingMode": "PAY_PER_REQUEST",
        "KeySchema": [{"AttributeName": a, "KeyType": t} for a, t in keys],
        "AttributeDefinitions": [{"AttributeName": a, "AttributeType": "N" if a in NUMERIC else "S"} for a in sorted(attrs)],
    }
    if gsis:
        kwargs["GlobalSecondaryIndexes"] = gsis
    ddb.create_table(**kwargs)


def zone_item(zone):
    """A zones-table item exactly as Terraform seeds it (polygon as a JSON string, numbers as N)."""
    return {
        "zone_id": zone["zone_id"], "name": zone["name"], "district": zone["district"], "hazard": zone["hazard"],
        "lat": Decimal(str(zone["lat"])), "lon": Decimal(str(zone["lon"])), "polygon": json.dumps(zone["polygon"]),
        "owner_cell": zone["owner_cell"], "warning_level_m": Decimal(str(zone["warning_level_m"])),
        "danger_level_m": Decimal(str(zone["danger_level_m"])), "gauge_cadence_s": zone["gauge_cadence_s"],
        "approval_timeout_s": zone["approval_timeout_s"], "is_test": zone["is_test"],
    }


class Stack:
    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.ddb = boto3.resource("dynamodb", region_name=REGION)
        self.s3 = boto3.client("s3", region_name=REGION)
        self.sqs = boto3.client("sqs", region_name=REGION)
        self.sns = boto3.client("sns", region_name=REGION)
        self.secrets = boto3.client("secretsmanager", region_name=REGION)

        for name, (h, r, idx) in TABLES.items():
            _create_table(self.ddb.meta.client, name, h, r, idx)
        for bucket in BUCKETS.values():
            self.s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION})
        self.queues = {t: self.sqs.create_queue(QueueName=f"t-a-{t}")["QueueUrl"] for t in TIERS}
        self.send_queues = {t: self.sqs.create_queue(QueueName=f"t-a-send-{t}")["QueueUrl"] for t in TIERS}
        self.topic_arn = self.sns.create_topic(Name="t-officers")["TopicArn"]
        self.secret_arn = self.secrets.create_secret(
            Name="t/sms-provider", SecretString=json.dumps({"api_key": "", "sender_id": "", "dlr_secret": "dlr-s3cret"})
        )["ARN"]

        with self.table("zones").batch_writer() as batch:
            for zone in ZONES:
                batch.put_item(Item=zone_item(zone))

        env = {
            "ZONES_TABLE": "t-zones", "STATE_TABLE": "t-zone-state", "ALERTS_TABLE": "t-alerts",
            "APPROVALS_TABLE": "t-approvals", "SUBSCRIBERS_TABLE": "t-subscribers", "INBOX_TABLE": "t-inbox",
            "RATELIMITS_TABLE": "t-ratelimits", "DELIVERIES_TABLE": "t-deliveries", "CELL": "a", "RULES_VERSION": "test-sha",
            "OFFICERS_TOPIC_ARN": self.topic_arn, "SMS_SECRET_ARN": self.secret_arn, "SMS_PROVIDER": "simulator",
            "IOT_ENDPOINT": f"data-ats.iot.{REGION}.amazonaws.com", "REPORT_PEPPER": "pepper", "STATE_MACHINE_ARN": "",
            "SCENARIO_FUNCTION": "t-scenario",
            **BUCKETS,
            **{f"QUEUE_{t.upper()}": url for t, url in self.queues.items()},
            **{f"SEND_QUEUE_{t.upper()}": url for t, url in self.send_queues.items()},
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

    def table(self, name):
        return self.ddb.Table(f"t-{name}")

    def load(self, module):
        """Import a handler module fresh, so its import-time clients bind to this mocked stack."""
        sys.modules.pop(module, None)
        return importlib.import_module(module)

    def messages(self, tier, queues=None):
        """Drain a queue (tier queue by default) and return the decoded message bodies."""
        url, bodies = (queues or self.queues)[tier], []
        while out := self.sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages", []):
            bodies += [json.loads(m["Body"]) for m in out]
            entries = [{"Id": str(i), "ReceiptHandle": m["ReceiptHandle"]} for i, m in enumerate(out)]
            self.sqs.delete_message_batch(QueueUrl=url, Entries=entries)
        return bodies

    def keys(self, bucket, prefix=""):
        return [o["Key"] for o in self.s3.list_objects_v2(Bucket=BUCKETS[bucket], Prefix=prefix).get("Contents", [])]

    def body(self, bucket, key):
        return self.s3.get_object(Bucket=BUCKETS[bucket], Key=key)["Body"].read().decode("utf-8")


@pytest.fixture
def stack(monkeypatch):
    for k, v in {"AWS_DEFAULT_REGION": REGION, "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                 "AWS_SECURITY_TOKEN": "testing", "AWS_SESSION_TOKEN": "testing"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        yield Stack(monkeypatch)


def api_event(method, resource, body=None, groups="", email="officer@test", path=None, query=None, headers=None):
    """An API Gateway (REST, Lambda proxy) event as the handlers receive it."""
    return {
        "httpMethod": method, "resource": resource, "pathParameters": path, "queryStringParameters": query,
        "headers": headers or {}, "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"claims": {"cognito:groups": groups, "email": email}}} if groups else {},
    }
