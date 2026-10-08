"""Public feed: writes public/zones.json (every real zone's tier and blind flag) to the web bucket, served
by CloudFront with a 5-second cache.

The public map reads this file instead of calling the API, so it keeps working through an API or Lambda
outage (static stability) and costs the same however many residents are refreshing it. Runs every minute
and right after any zone's tier changes (the scorers invoke it).

Environment: STATE_TABLE, WEB_BUCKET.
"""

import json
import os
import time

import boto3

ddb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
states = ddb.Table(os.environ["STATE_TABLE"])
KEY = "public/zones.json"
TEST_ZONE_PREFIXES = ("T", "H", "L")


def handler(event, context):
    zones, kwargs = [], {"ProjectionExpression": "zone_id, tier, blind, updated_at"}
    while True:
        page = states.scan(**kwargs)
        zones += [
            {"zone_id": z["zone_id"], "tier": z.get("tier", "normal"), "blind": bool(z.get("blind")),
             "updated_at": int(z["updated_at"]) if z.get("updated_at") is not None else None}
            for z in page["Items"] if not z["zone_id"].startswith(TEST_ZONE_PREFIXES)
        ]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    body = {"generated_at": int(time.time()), "zones": sorted(zones, key=lambda z: z["zone_id"])}
    s3.put_object(Bucket=os.environ["WEB_BUCKET"], Key=KEY, Body=json.dumps(body).encode("utf-8"),
                  ContentType="application/json", CacheControl="public, max-age=5")
    return {"zones": len(zones)}
