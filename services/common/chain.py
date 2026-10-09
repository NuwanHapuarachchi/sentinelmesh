"""Hash chain over write-once records in S3: tamper evidence without Object Lock (the account forbids it).

Every record gets a chain entry decisions/chain/{seq:012d}.json holding its key, its SHA-256 and the
SHA-256 of the previous entry. S3's conditional write (If-None-Match) is the lock: only one writer can
create entry n, so concurrent issuers in different cells still produce one ordered chain. A small head item
(seq and hash of the last entry) in the meta table is only a hint; a stale or lost hint is repaired from the
entries themselves.

verify() walks the chain and reports gaps, edited or deleted records, broken links, and records that never
made it into the chain. Editing or deleting any record, or any entry, is therefore visible. Records S3 stored
before the first chain entry (LastModified, which S3 sets) predate chaining and are counted as legacy.
"""

import hashlib
import json
import time
from datetime import UTC, datetime

from botocore.exceptions import ClientError

GENESIS = "0" * 64
HEAD = "decision-chain"
CHAIN_PREFIX = "decisions/chain/"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def entry_key(seq, prefix=CHAIN_PREFIX):
    return f"{prefix}{seq:012d}.json"


def _code(exc):
    return exc.response["Error"]["Code"]


def advance(meta, seq, sha, head=HEAD):
    """Move the head hint forward to (seq, sha) unless it is already there or beyond."""
    try:
        meta.update_item(
            Key={"k": head}, UpdateExpression="SET #seq = :s, #sha = :h",
            ConditionExpression="attribute_not_exists(#seq) OR #seq < :s",
            ExpressionAttributeNames={"#seq": "seq", "#sha": "sha256"}, ExpressionAttributeValues={":s": seq, ":h": sha},
        )
    except ClientError as exc:
        if _code(exc) != "ConditionalCheckFailedException":
            raise


def append(s3, bucket, meta, record_key, record_raw, at=None, head=HEAD, prefix=CHAIN_PREFIX, attempts=25):
    """Link one already-written record into the chain. Returns {"seq", "key", "sha256"} of its entry."""
    record_sha = digest(record_raw)
    for _ in range(attempts):
        item = meta.get_item(Key={"k": head}, ConsistentRead=True).get("Item") or {"seq": 0, "sha256": GENESIS}
        seq = int(item["seq"]) + 1
        entry = {"seq": seq, "prev_sha256": item["sha256"], "record_key": record_key, "record_sha256": record_sha,
                 "at": datetime.fromtimestamp(at or time.time(), UTC).isoformat()}
        raw = json.dumps(entry, sort_keys=True).encode("utf-8")
        key = entry_key(seq, prefix)
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=raw, IfNoneMatch="*", ContentType="application/json")
        except ClientError as exc:
            if _code(exc) == "PreconditionFailed":
                # Entry seq already exists (another writer, or one that died before moving the hint):
                # learn its hash, move the hint past it, and try the next number.
                existing = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                advance(meta, seq, digest(existing), head)
                continue
            if _code(exc) == "ConditionalRequestConflict":  # someone is writing entry seq right now
                time.sleep(0.05)
                continue
            raise
        sha = digest(raw)
        advance(meta, seq, sha, head)
        return {"seq": seq, "key": key, "sha256": sha}
    raise RuntimeError(f"could not append {record_key} to the decision chain")


def _objects(s3, bucket, prefix):
    """{key: LastModified} under prefix."""
    found, kwargs = {}, {"Bucket": bucket, "Prefix": prefix}
    while True:
        page = s3.list_objects_v2(**kwargs)
        found |= {o["Key"]: o["LastModified"] for o in page.get("Contents", [])}
        if not page.get("IsTruncated"):
            return found
        kwargs["ContinuationToken"] = page["NextContinuationToken"]


def verify(s3, bucket, records_prefix="decisions/", prefix=CHAIN_PREFIX):
    """Walk the whole chain. Returns {"entries", "records", "legacy", "ok", "problems": [...]}."""
    problems, linked = [], set()
    prev, expected = GENESIS, 1
    chained = _objects(s3, bucket, prefix)
    entries = sorted(chained)
    for key in entries:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        try:
            entry = json.loads(raw)
        except ValueError:
            problems.append(f"{key}: not JSON")
            continue
        if entry.get("seq") != expected or key != entry_key(expected, prefix):
            problems.append(f"{key}: expected entry {expected} (missing or out of order)")
            expected = int(entry.get("seq", expected))
        if entry.get("prev_sha256") != prev:
            problems.append(f"{key}: does not link to the previous entry")
        linked.add(entry.get("record_key"))
        try:
            record = s3.get_object(Bucket=bucket, Key=entry["record_key"])["Body"].read()
            if digest(record) != entry.get("record_sha256"):
                problems.append(f"{entry['record_key']}: changed after it was recorded (entry {entry.get('seq')})")
        except ClientError as exc:
            if _code(exc) not in ("NoSuchKey", "404"):
                raise
            problems.append(f"{entry['record_key']}: deleted (entry {entry.get('seq')})")
        prev, expected = digest(raw), expected + 1
    records = {k: t for k, t in _objects(s3, bucket, records_prefix).items() if not k.startswith(prefix)}
    chain_start = min(chained.values()) if chained else None
    legacy = {k for k, t in records.items() if k not in linked and (chain_start is None or t < chain_start)}
    problems += [f"{k}: not in the chain" for k in sorted(records) if k not in linked and k not in legacy]
    return {"entries": len(entries), "records": len(records) - len(legacy), "legacy": len(legacy),
            "ok": not problems, "problems": problems}
