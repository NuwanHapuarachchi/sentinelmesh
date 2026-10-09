"""SMS events: AWS End User Messaging delivery events (via the configuration set's SNS destination) turned
into receipts. DELIVERED counts as a confirmed delivery (ledger, alert count, time to first alert); a final
failure marks the ledger row UNDELIVERED, which the reach monitor counts against the delivery rate.

Environment: INBOX_TABLE, DELIVERIES_TABLE, ALERTS_TABLE.
"""

import json
import os

import boto3

from common import receipts, sms
from common.observe import log, metric

ddb = boto3.resource("dynamodb")
inbox = ddb.Table(os.environ["INBOX_TABLE"])
deliveries = ddb.Table(os.environ["DELIVERIES_TABLE"])
alerts = ddb.Table(os.environ["ALERTS_TABLE"])


def handler(event, context):
    outcomes = []
    for record in event.get("Records", []):
        message = json.loads(record["Sns"]["Message"])
        ref, outcome, status = sms.parse_eum_event(message)
        metric("SmsDeliveryReports", 1, Status=status or "UNKNOWN")
        if outcome == "DELIVERED":
            outcomes.append(receipts.record_for_ref(inbox, deliveries, alerts, ref, "sms"))
        elif outcome == "FAILED":
            outcomes.append({"undelivered": receipts.undelivered(deliveries, ref, status, message.get("messageStatusDescription", ""))})
        else:
            outcomes.append({"progress": status})
        log("sms_event", ref=ref, status=status, outcome=outcome, message_id=message.get("messageId"))
    return {"outcomes": outcomes}
