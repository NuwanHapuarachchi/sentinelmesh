import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from common import cap, messages

ZONES = json.loads((Path(__file__).resolve().parents[2] / "terraform" / "app" / "data" / "zones.json").read_text())
LONGEST_ID = max((z["zone_id"] for z in ZONES), key=len)
NS = {"c": cap.NS}


@pytest.mark.parametrize("tier", ["imminent", "warning", "advisory"])
@pytest.mark.parametrize("hazard", ["flood", "landslide"])
def test_every_template_fits_one_sms_segment(tier, hazard):
    for lang in messages.TEMPLATES:
        text = messages.render(lang, tier, hazard, LONGEST_ID, "ABC123")
        encoding, units, parts = messages.segments(text)
        assert parts == 1, f"{lang}/{tier}/{hazard}: {encoding} {units} units"


def test_english_is_gsm7_and_sinhala_tamil_are_ucs2():
    assert messages.segments(messages.render("en", "warning", "flood", "Z01", "R"))[0] == "GSM-7"
    assert messages.segments(messages.render("si", "warning", "flood", "Z01", "R"))[0] == "UCS-2"
    assert messages.segments(messages.render("ta", "warning", "flood", "Z01", "R"))[0] == "UCS-2"


def test_segment_maths():
    assert messages.segments("a" * 160) == ("GSM-7", 160, 1)
    assert messages.segments("a" * 161) == ("GSM-7", 161, 2)
    assert messages.segments("€" * 80) == ("GSM-7", 160, 1)
    assert messages.segments("ක" * 70)[2] == 1
    assert messages.segments("ක" * 71)[2] == 2


def test_cap_document_is_valid_shape():
    zone = ZONES[0]
    xml = cap.build("SM-20261008-Z01-0001", zone, "imminent", "flood", 1_791_400_000)
    root = ET.fromstring(xml)
    assert root.tag == f"{{{cap.NS}}}alert"
    assert [c.tag.split("}")[1] for c in root][:6] == ["identifier", "sender", "sent", "status", "msgType", "scope"]
    sent = root.find("c:sent", NS).text
    assert sent.endswith("+05:30") and "Z" not in sent
    infos = root.findall("c:info", NS)
    assert [i.find("c:language", NS).text for i in infos] == ["en-LK", "si-LK", "ta-LK"]
    assert infos[0].find("c:urgency", NS).text == "Immediate"
    assert infos[0].find("c:responseType", NS).text == "Evacuate"


def test_cap_polygon_is_closed_lat_lon():
    zone = ZONES[0]
    text = cap.polygon_text(zone["polygon"])
    pairs = text.split(" ")
    assert pairs[0] == pairs[-1] and len(pairs) >= 4
    lat, lon = map(float, pairs[0].split(","))
    assert 5 < lat < 10 and 79 < lon < 82  # Sri Lanka: lat first


def test_drills_are_exercises():
    root = ET.fromstring(cap.build("X", ZONES[0], "warning", "landslide", 1_791_400_000, status="Exercise"))
    assert root.find("c:status", NS).text == "Exercise"
    assert root.find("c:info/c:category", NS).text == "Geo"
