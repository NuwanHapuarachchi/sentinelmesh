"""API Gateway (REST, Lambda proxy) helpers."""

import json
from decimal import Decimal


def _default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(type(o))


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization,Content-Type,X-Device-Token",
            "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
        },
        "body": json.dumps(body, default=_default, ensure_ascii=False),
    }


def claims_of(event):
    """Cognito ID-token claims from a REST API authorizer ({} without one)."""
    return ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}


def groups_of(event):
    """Cognito groups from a REST API authorizer; the claim arrives as 'a,b' or '[a b]'."""
    raw = claims_of(event).get("cognito:groups", "")
    if isinstance(raw, list):
        return set(raw)
    return {g for g in raw.strip("[]").replace(",", " ").split() if g}
