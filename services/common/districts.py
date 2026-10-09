"""District names as used in IoT thing names, attributes and topics, which allow no spaces."""

import re


def slug(district):
    """'Nuwara Eliya' -> 'nuwara-eliya'. Terraform derives the same: trim(replace(lower(d), "/[^a-z0-9]+/", "-"), "-")."""
    return re.sub(r"[^a-z0-9]+", "-", district.lower()).strip("-")
