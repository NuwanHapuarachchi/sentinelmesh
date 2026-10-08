"""Trilingual alert templates and SMS segment maths.

Sinhala and Tamil need UCS-2 SMS: 70 UTF-16 units in one segment (67 per part when split).
English stays within GSM-7: 160 characters. Templates are written to fit one segment, so every
recipient gets exactly one message per alert. (Sinhala/Tamil wording needs native-speaker review.)
"""

GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXT = set("^{}\\[~]|€")

TEMPLATES = {
    "en": {
        "imminent": "EMERGENCY {zone}: {hazard} imminent. Move to safe high ground NOW. Do not wait. Ref {ref}",
        "warning": "WARNING {zone}: {hazard} likely in the next hours. Prepare to evacuate. Ref {ref}",
        "advisory": "ADVISORY {zone}: {hazard} risk rising. Stay alert and follow official updates. Ref {ref}",
        "clear": "ALL CLEAR {zone}: {hazard} danger has passed. Follow official advice before returning. Ref {ref}",
    },
    "si": {
        "imminent": "හදිසි {zone}: {hazard} ආසන්නයි. දැන්ම උස් ආරක්ෂිත ස්ථානයකට යන්න.",
        "warning": "අනතුරු ඇඟවීම {zone}: {hazard} අවදානම. ඉවත්වීමට සූදානම් වන්න.",
        "advisory": "දැනුම්දීම {zone}: {hazard} අවදානම ඉහළයි. සැලකිලිමත් වන්න.",
        "clear": "අවදානම අවසන් {zone}: {hazard} අනතුර පහව ගොස් ඇත. නිල උපදෙස් පිළිපදින්න.",
    },
    "ta": {
        "imminent": "அவசரம் {zone}: {hazard} உடனடி. உடனே உயரமான இடத்துக்கு செல்லவும்.",
        "warning": "எச்சரிக்கை {zone}: {hazard} அபாயம். வெளியேற தயாராகுங்கள்.",
        "advisory": "அறிவிப்பு {zone}: {hazard} அபாயம் உயர்கிறது. கவனமாக இருங்கள்.",
        "clear": "அபாயம் நீங்கியது {zone}: {hazard} ஆபத்து கடந்தது. அறிவுரையை கேளுங்கள்.",
    },
}

HAZARD_WORDS = {
    "en": {"flood": "Flood", "landslide": "Landslide"},
    "si": {"flood": "ගංවතුර", "landslide": "නායයෑම"},
    "ta": {"flood": "வெள்ளம்", "landslide": "நிலச்சரிவு"},
}


def is_gsm7(text):
    return all(c in GSM7 or c in GSM7_EXT for c in text)


def segments(text):
    """(encoding, units, segment count) as a carrier would bill it."""
    if is_gsm7(text):
        units = sum(2 if c in GSM7_EXT else 1 for c in text)
        return "GSM-7", units, 1 if units <= 160 else -(-units // 153)
    units = len(text.encode("utf-16-le")) // 2
    return "UCS-2", units, 1 if units <= 70 else -(-units // 67)


def render(lang, tier, hazard, zone, ref):
    return TEMPLATES[lang][tier].format(zone=zone, hazard=HAZARD_WORDS[lang][hazard], ref=ref)


def render_all(tier, hazard, zone, ref):
    return {lang: render(lang, tier, hazard, zone, ref) for lang in TEMPLATES}
