"""Duty-officer approvals for warning-tier alerts.

Invoked two ways:
- Step Functions (lambda:invoke.waitForTaskToken) with op=approval_request: stores the task
  token, emails officers (the email never contains the token), and returns.
- API Gateway (Cognito duty_officer group): GET /approvals lists pending ones with evidence;
  POST /approvals/{id} {"decision": "approve"|"reject", "note": "..."} resumes the workflow.

If nobody decides before the zone's approval timeout, the workflow issues the alert anyway
(fail toward alerting) and the approval becomes AUTO_ISSUED.
Environment: APPROVALS_TABLE, OFFICERS_TOPIC_ARN.
"""

import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common.api import groups_of, respond
from common.observe import log

ddb = boto3.resource("dynamodb")
sfn = boto3.client("stepfunctions")
sns = boto3.client("sns")
approvals = ddb.Table(os.environ["APPROVALS_TABLE"])


def handler(event, context):
    if event.get("op") == "approval_request":
        return request(event)
    if "duty_officer" not in groups_of(event):
        return respond(403, {"error": "duty_officer group required"})
    if event.get("httpMethod") == "GET":
        return list_pending()
    return decide(event)


def request(event):
    inp, now = event["input"], int(time.time())
    timeout = int(inp.get("approval_timeout_s", 300))
    approvals.put_item(Item={
        "approval_id": event["approval_id"], "task_token": event["token"], "status": "PENDING",
        "pending": "PENDING", "created_at": now, "deadline": now + timeout,
        "zone_id": inp["zone_id"], "tier": inp["tier"], "hazard": inp.get("hazard", "flood"),
        "cell": inp.get("cell", ""), "summary": json.dumps({
            k: inp.get(k) for k in ("score", "reason", "agreeing", "contributions", "evidence", "blind", "drill")
        }, default=str),
    })
    if os.environ.get("OFFICERS_TOPIC_ARN") and not inp.get("is_test"):
        sns.publish(
            TopicArn=os.environ["OFFICERS_TOPIC_ARN"],
            Subject=f"SentinelMesh: approve WARNING for {inp['zone_id']} within {timeout}s",
            Message=(f"A {inp.get('hazard', 'flood')} WARNING for zone {inp['zone_id']} needs approval.\n"
                     f"Score {inp.get('score')}; agreeing sources: {', '.join(inp.get('agreeing') or [])}.\n"
                     f"If nobody decides within {timeout} seconds the alert is issued automatically.\n"
                     "Open the SentinelMesh officer console to approve or reject."),
        )
    log("approval_requested", approval_id=event["approval_id"], zone_id=inp["zone_id"], timeout=timeout)
    return {"requested": True}


def list_pending():
    items = approvals.query(IndexName="pending", KeyConditionExpression=Key("pending").eq("PENDING"))["Items"]
    now = int(time.time())
    return respond(200, {"approvals": [
        {k: v for k, v in i.items() if k != "task_token"} | {"seconds_left": max(0, int(i["deadline"]) - now)}
        for i in items
    ]})


def decide(event):
    approval_id = (event.get("pathParameters") or {}).get("id")
    body = json.loads(event.get("body") or "{}")
    decision = body.get("decision")
    if decision not in ("approve", "reject"):
        return respond(400, {"error": "decision must be approve or reject"})
    if decision == "reject" and not body.get("note"):
        return respond(400, {"error": "a rejection needs a note"})
    officer = (event.get("requestContext", {}).get("authorizer", {}).get("claims", {}) or {}).get("email", "officer")

    try:  # claim it first so two officers can't both decide
        item = approvals.update_item(
            Key={"approval_id": approval_id},
            UpdateExpression="SET #s = :d, decided_by = :o, note = :n REMOVE pending",
            ConditionExpression="#s = :p",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":d": "DECIDING", ":o": officer, ":n": body.get("note", ""), ":p": "PENDING"},
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return respond(409, {"error": "already decided or auto-issued"})
        raise

    output = {"decision": decision, "decided_by": officer, "note": body.get("note", ""), "approval_id": approval_id}
    try:
        sfn.send_task_success(taskToken=item["task_token"], output=json.dumps(output))
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("TaskTimedOut", "TaskDoesNotExist", "InvalidToken"):
            approvals.update_item(Key={"approval_id": approval_id}, UpdateExpression="SET #s = :a",
                                  ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":a": "AUTO_ISSUED"})
            return respond(409, {"error": "approval window expired; the alert was issued automatically"})
        raise
    approvals.update_item(Key={"approval_id": approval_id}, UpdateExpression="SET #s = :d",
                          ExpressionAttributeNames={"#s": "status"},
                          ExpressionAttributeValues={":d": "APPROVED" if decision == "approve" else "REJECTED"})
    log("approval_decided", approval_id=approval_id, decision=decision, officer=officer)
    return respond(200, {"approval_id": approval_id, "decision": decision})
