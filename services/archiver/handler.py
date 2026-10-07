"""Archiver: the observation stream's second consumer. Writes every reading to the evidence lake as
gzipped JSON lines, partitioned for Athena, so any alert can be replayed and audited after the event.
The object key comes from the batch's first sequence number, so a retry overwrites instead of duplicating.

Environment: EVIDENCE_BUCKET, CELL.
"""

import gzip
import json
import os
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.types import TypeDeserializer

s3 = boto3.client("s3")
_deser = TypeDeserializer()


def handler(event, context):
    records = [r for r in event.get("Records", []) if r.get("dynamodb", {}).get("NewImage")]
    if not records:
        return {"archived": 0}
    rows = []
    for r in records:
        item = {k: _deser.deserialize(v) for k, v in r["dynamodb"]["NewImage"].items()}
        rows.append(json.dumps(item, default=float))
    first = records[0]["dynamodb"]
    when = datetime.fromtimestamp(first.get("ApproximateCreationDateTime", 0), UTC)
    key = (f"obs/cell={os.environ['CELL']}/dt={when:%Y-%m-%d}/hour={when:%H}/"
           f"{first['SequenceNumber']}.jsonl.gz")
    s3.put_object(Bucket=os.environ["EVIDENCE_BUCKET"], Key=key, Body=gzip.compress("\n".join(rows).encode()),
                  ContentType="application/x-ndjson", ContentEncoding="gzip")
    return {"archived": len(rows), "key": key}
