"""Verify the decision-record hash chain (read-only). Exits 1 if any record was changed or deleted, the chain
has a gap or a broken link, or a record never made it into the chain.

    python scripts/verify_decisions.py [--bucket sentinelmesh-decisions-<account>]
"""

import argparse
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services"))
from common import chain  # noqa: E402

REGION = "ap-southeast-2"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", help="decisions bucket (default: sentinelmesh-decisions-<account>)")
    args = parser.parse_args(argv)
    session = boto3.session.Session(region_name=REGION)
    bucket = args.bucket or f"sentinelmesh-decisions-{session.client('sts').get_caller_identity()['Account']}"
    report = chain.verify(session.client("s3"), bucket)
    print(f"{bucket}: {report['entries']} chain entries, {report['records']} records")
    for problem in report["problems"]:
        print(f"  PROBLEM {problem}")
    print("chain intact" if report["ok"] else "CHAIN BROKEN")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
