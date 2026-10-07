"""Structured JSON logs and CloudWatch Embedded Metric Format (EMF) without extra dependencies."""

import json
import time

NAMESPACE = "SentinelMesh"


def log(event, **fields):
    print(json.dumps({"event": event, **fields}, default=str))


def metric(name, value, unit="Count", **dimensions):
    """One EMF line: CloudWatch turns it into a metric with the given dimensions."""
    dims = {k: str(v) for k, v in dimensions.items()}
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": NAMESPACE,
                            "Dimensions": [list(dims)] if dims else [[]],
                            "Metrics": [{"Name": name, "Unit": unit}],
                        }
                    ],
                },
                name: value,
                **dims,
            }
        )
    )
