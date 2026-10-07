"""CAP 1.2 (OASIS Common Alerting Protocol, ITU-T X.1303) documents for issued alerts.

One <info> block per language, elements in schema order, timestamps with the +05:30 offset
(CAP forbids "Z"), polygons as closed "lat,lon" pairs.
"""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone

from common.messages import HAZARD_WORDS, render

NS = "urn:oasis:names:tc:emergency:cap:1.2"
SLT = timezone(timedelta(hours=5, minutes=30))
SENDER = "sentinelmesh@thebyteforce.example"

TIER_CODES = {
    # tier: (urgency, severity, certainty, responseType)
    "imminent": ("Immediate", "Extreme", "Observed", "Evacuate"),
    "warning": ("Expected", "Severe", "Likely", "Prepare"),
    "advisory": ("Expected", "Moderate", "Possible", "Monitor"),
}
CATEGORY = {"flood": "Met", "landslide": "Geo"}
LANG_TAGS = {"en": "en-LK", "si": "si-LK", "ta": "ta-LK"}


def cap_time(epoch_s):
    return datetime.fromtimestamp(epoch_s, UTC).astimezone(SLT).replace(microsecond=0).isoformat()


def polygon_text(lonlat_ring):
    """GeoJSON ring [[lon, lat], ...] -> CAP 'lat,lon lat,lon ...', closed."""
    ring = [list(p) for p in lonlat_ring]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return " ".join(f"{lat},{lon}" for lon, lat in ring)


def build(alert_id, zone, tier, hazard, sent_epoch, status="Actual", expires_hours=6):
    urgency, severity, certainty, response = TIER_CODES[tier]
    ET.register_namespace("", NS)
    root = ET.Element(f"{{{NS}}}alert")

    def sub(parent, tag, text=None):
        el = ET.SubElement(parent, f"{{{NS}}}{tag}")
        if text is not None:
            el.text = str(text)
        return el

    sub(root, "identifier", alert_id)
    sub(root, "sender", SENDER)
    sub(root, "sent", cap_time(sent_epoch))
    sub(root, "status", status)
    sub(root, "msgType", "Alert")
    sub(root, "scope", "Public")

    for lang, tag in LANG_TAGS.items():
        info = sub(root, "info")
        sub(info, "language", tag)
        sub(info, "category", CATEGORY[hazard])
        sub(info, "event", f"{HAZARD_WORDS[lang][hazard]} {tier}")
        sub(info, "responseType", response)
        sub(info, "urgency", urgency)
        sub(info, "severity", severity)
        sub(info, "certainty", certainty)
        sub(info, "expires", cap_time(sent_epoch + expires_hours * 3600))
        sub(info, "senderName", "SentinelMesh Disaster Early Warning")
        sub(info, "headline", f"{tier.upper()} - {zone['name']}")
        sub(info, "description", render(lang, tier, hazard, zone["zone_id"], alert_id[-6:]))
        area = sub(info, "area")
        sub(area, "areaDesc", f"{zone['name']}, {zone['district']} District")
        sub(area, "polygon", polygon_text(zone["polygon"]))

    return ET.tostring(root, encoding="unicode", xml_declaration=False)
