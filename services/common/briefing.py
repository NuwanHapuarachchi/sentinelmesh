"""Optional Gemini evidence interpretation. Never issues alerts or changes zone state."""
import json
import math
import re
from decimal import Decimal
from urllib.request import Request, urlopen

VERSION = "zone-briefing-v1"
SECTIONS = ("insights", "anomalies", "outlook")


def number(value):
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) and math.isfinite(value)


def snapshot(zone, state, now):
    doc = json.loads(state.get("doc") or "{}")
    facts = {"assessment": {"tier": state.get("tier", "unknown"), "blind": bool(state.get("blind")),
                            "observed_at": int(state.get("updated_at", 0))}}
    for cls in ("gauge", "rain", "tilt", "forecast"):
        ev = doc.get("ev", {}).get(cls, {})
        if number(ev.get("value")) and number(ev.get("ts")):
            facts[cls] = {"value": ev["value"], "observed_at": ev["ts"],
                          "age_seconds": max(0, int(now - ev["ts"]))}
    history = sorted({float(x[0]): float(x[1]) for x in doc.get("gauge_hist", [])
                      if isinstance(x, list) and len(x) == 2 and all(number(v) for v in x)
                      and 0 <= now - x[0] <= 3600}.items())[-6:]
    # No resident reports, identifiers, location coordinates or free text leave AWS.
    if history:
        facts["gauge_history"] = {"readings": history, "unit": "metres", "time_unit": "unix_seconds"}
    for key in ("warning_level_m", "danger_level_m"):
        if number(zone.get(key)):
            facts[key] = {"value": float(zone[key]), "unit": "metres"}
    sufficient = len(history) >= 3 and history[-1][0] - history[0][0] >= 120
    return {"facts": facts, "history_sufficient": sufficient, "captured_at": int(now)}


def validate(output, data):
    if not isinstance(output, dict) or set(output) != set(SECTIONS):
        raise ValueError("invalid sections")
    for section in SECTIONS:
        items = output[section]
        if not isinstance(items, list) or not 1 <= len(items) <= 3:
            raise ValueError("invalid items")
        for item in items:
            if not isinstance(item, dict) or set(item) != {"text", "evidence"}:
                raise ValueError("invalid item")
            text, refs = item["text"], item["evidence"]
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 300:
                raise ValueError("invalid text")
            if not isinstance(refs, list) or not 1 <= len(refs) <= 6 or any(
                not isinstance(ref, str) or ref not in data["facts"] for ref in refs
            ):
                raise ValueError("unknown evidence")
            # Prohibit invented probabilities, timelines and other generated numerical claims.
            if re.search(r"\d|%", text):
                raise ValueError("numerical claim")
            if re.search(r"evacuat|all.clear|safe to|suppress|cancel.{0,20}warn|issue.{0,20}warn", text, re.IGNORECASE):
                raise ValueError("warning instruction")
    if not data["history_sufficient"]:
        output["outlook"] = [{"text": "Insufficient recent gauge history for a trend outlook.", "evidence": ["assessment"]}]
    return output


def generate(data, key, model):
    if not re.fullmatch(r"gemini-[a-zA-Z0-9.\-]+", model):
        raise ValueError("invalid model")
    item = {"type": "object", "properties": {"text": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string", "enum": list(data["facts"])}}},
            "required": ["text", "evidence"], "additionalProperties": False}
    schema = {"type": "object", "properties": {s: {"type": "array", "items": item,
              "minItems": 1, "maxItems": 3} for s in SECTIONS}, "required": list(SECTIONS)}
    instruction = (
        "You provide read-only officer evidence briefings, not public safety advice. "
        "Use only supplied facts. Insights explain the existing assessment; anomalies flag possible "
        "inconsistency for human review, never diagnose faults or discard real hazards. Outlook is "
        "qualitative trend interpretation, not a validated forecast. State uncertainty and missing data. "
        "Never recommend a tier change, warning suppression, evacuation or all-clear. No invented "
        "facts, confidence percentages, numeric claims or times to flooding. Write one to three short "
        "sentences per section, with evidence IDs. If history is insufficient, say so."
    )
    body = {"systemInstruction": {"parts": [{"text": instruction}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(data)}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1500,
                                 "responseMimeType": "application/json", "responseJsonSchema": schema}}
    req = Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                  data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urlopen(req, timeout=10) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("response too large")
    candidate = json.loads(raw)["candidates"][0]
    if candidate.get("finishReason") != "STOP":
        raise ValueError("incomplete response")
    text = "".join(p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought"))
    return validate(json.loads(text), data)
