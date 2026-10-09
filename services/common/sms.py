"""SMS provider adapter.

SMS_PROVIDER=simulator (default): the message is written to the simulated handset inbox.
SMS_PROVIDER=eum: AWS End User Messaging SMS (send_eum). Our reference travels in the message Context and
comes back in the delivery events (parse_eum_event), which the sms_events function turns into receipts.
  EUM_ORIGINATION_IDENTITY  sender id, phone number or pool to send from (optional in the sandbox)
  EUM_CONFIGURATION_SET     configuration set whose event destination publishes delivery events
SMS_PROVIDER=http: a third-party gateway, configured by environment variables plus a Secrets
Manager secret (JSON: {"api_key": ..., "sender_id": ..., "dlr_secret": ...}) so no key ever
sits in Terraform state:

  SMS_HTTP_URL           gateway send URL
  SMS_HTTP_FORMAT        json | form                     (default json)
  SMS_HTTP_TO_FIELD      recipient field                 (default "to")
  SMS_HTTP_TEXT_FIELD    message field                   (default "message")
  SMS_HTTP_SENDER_FIELD  sender id field                 (default "sender_id")
  SMS_HTTP_REF_FIELD     our reference field, echoed in delivery reports (default "ref")
  SMS_HTTP_UNICODE_FIELD field set to 1/"unicode" for Sinhala/Tamil (optional)
  SMS_HTTP_AUTH          bearer | header | query | body  (default bearer)
  SMS_HTTP_AUTH_FIELD    header/query/body field name for the key (default "api_key")
  SMS_HTTP_ID_PATH       dotted path to the provider message id in the JSON reply (optional)
"""

import hmac
import json
import os
import urllib.parse
import urllib.request

from common.messages import is_gsm7


def normalise_msisdn(number):
    """Sri Lankan numbers to E.164 (+94...)."""
    digits = "".join(c for c in str(number) if c.isdigit())
    if digits.startswith("94"):
        return "+" + digits
    if digits.startswith("0"):
        return "+94" + digits[1:]
    if len(digits) == 9:
        return "+94" + digits
    return "+" + digits


def build_request(to, text, ref, secret, env=None):
    """Returns (url, headers, body bytes) for the http provider. Pure, so it is unit-tested."""
    env = env if env is not None else os.environ
    fmt = env.get("SMS_HTTP_FORMAT", "json")
    auth = env.get("SMS_HTTP_AUTH", "bearer")
    auth_field = env.get("SMS_HTTP_AUTH_FIELD", "api_key")
    url = env["SMS_HTTP_URL"]
    fields = {
        env.get("SMS_HTTP_TO_FIELD", "to"): normalise_msisdn(to),
        env.get("SMS_HTTP_TEXT_FIELD", "message"): text,
        env.get("SMS_HTTP_REF_FIELD", "ref"): ref,
    }
    if secret.get("sender_id"):
        fields[env.get("SMS_HTTP_SENDER_FIELD", "sender_id")] = secret["sender_id"]
    if env.get("SMS_HTTP_UNICODE_FIELD") and not is_gsm7(text):
        fields[env["SMS_HTTP_UNICODE_FIELD"]] = "1"

    headers = {}
    if auth == "bearer":
        headers["Authorization"] = f"Bearer {secret['api_key']}"
    elif auth == "header":
        headers[auth_field] = secret["api_key"]
    elif auth == "query":
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({auth_field: secret["api_key"]})
    elif auth == "body":
        fields[auth_field] = secret["api_key"]

    if fmt == "form":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urllib.parse.urlencode(fields).encode()
    else:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body = json.dumps(fields, ensure_ascii=False).encode("utf-8")
    return url, headers, body


def extract_id(reply_text, path):
    if not path:
        return None
    try:
        node = json.loads(reply_text)
        for part in path.split("."):
            node = node[int(part)] if isinstance(node, list) else node[part]
        return str(node)
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def send_http(to, text, ref, secret, env=None, timeout=5):
    env = env if env is not None else os.environ
    url, headers, body = build_request(to, text, ref, secret, env)
    last_error = None
    for _ in range(3):  # retry 5xx / network errors twice
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL comes from config
                reply = resp.read().decode("utf-8", "replace")
                return {"status": "SENT", "provider_id": extract_id(reply, env.get("SMS_HTTP_ID_PATH"))}
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return {"status": "REJECTED", "error": f"HTTP {exc.code}"}
            last_error = f"HTTP {exc.code}"
        except OSError as exc:
            last_error = str(exc)
    return {"status": "FAILED", "error": last_error}


DLR_STATUS = {
    "delivered": "DELIVERED", "deliv": "DELIVERED", "success": "DELIVERED", "1": "DELIVERED",
    "failed": "FAILED", "undeliv": "FAILED", "rejected": "FAILED", "expired": "FAILED", "2": "FAILED",
}


def parse_dlr(params):
    """Delivery report from the gateway -> (ref, status). Accepts common field names."""
    ref = params.get("ref") or params.get("reference") or params.get("client_ref") or params.get("msg_ref")
    raw = str(params.get("status") or params.get("dlr_status") or params.get("state") or "").lower()
    return ref, DLR_STATUS.get(raw, "UNKNOWN")


def dlr_secret_ok(provided, expected):
    return bool(provided) and bool(expected) and hmac.compare_digest(str(provided), str(expected))


# ---------- AWS End User Messaging SMS ----------

# Errors worth retrying later (the sender leaves the recipient FAILED and SQS retries the batch).
EUM_TRANSIENT = {"ThrottlingException", "InternalServerException", "ServiceQuotaExceededException"}


def send_eum(to, text, ref, client, origination_identity=None, configuration_set=None):
    """Send one transactional SMS. Returns {"status": SENT|FAILED|REJECTED, ...} like send_http."""
    request = {"DestinationPhoneNumber": normalise_msisdn(to), "MessageBody": text, "MessageType": "TRANSACTIONAL",
               "Context": {"ref": ref}}
    if origination_identity:
        request["OriginationIdentity"] = origination_identity
    if configuration_set:
        request["ConfigurationSetName"] = configuration_set
    try:
        return {"status": "SENT", "provider_id": client.send_text_message(**request)["MessageId"]}
    except Exception as exc:  # botocore ClientError
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        return {"status": "FAILED" if code in EUM_TRANSIENT else "REJECTED", "error": code}


# messageStatus values that are final failures; DELIVERED means the handset accepted the message, the rest
# (SUCCESSFUL = accepted by the carrier, PENDING, QUEUED, SENT, ACCEPTED) are progress, not receipts.
EUM_FAILED = {"INVALID", "UNREACHABLE", "BLOCKED", "CARRIER_UNREACHABLE", "SPAM", "INVALID_MESSAGE", "CARRIER_BLOCKED",
              "TTL_EXPIRED", "FAILED", "UNROUTABLE", "PROTECT_BLOCKED"}


def parse_eum_event(event):
    """An End User Messaging SMS event -> (ref, outcome, network status); outcome is DELIVERED, FAILED or None."""
    status = str(event.get("messageStatus", "")).upper()
    ref = (event.get("context") or {}).get("ref")
    if status == "DELIVERED":
        return ref, "DELIVERED", status
    if status in EUM_FAILED or (status == "UNKNOWN" and event.get("isFinal")):
        return ref, "FAILED", status
    return ref, None, status

