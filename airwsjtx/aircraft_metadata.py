"""Optional readsb/ADS-B Exchange metadata; absent categories remain unknown."""
from __future__ import annotations

import re


def normalize_category(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    category = value.strip().upper()
    return category if re.fullmatch(r"[ABC][1-7]", category) else None


def network_metadata(item: dict) -> dict:
    result = {"category": normalize_category(item.get("category"))}
    for field, key in (("type_code", "t"), ("registration", "r"), ("description", "desc"),
                       ("squawk", "squawk"), ("emergency", "emergency")):
        value = item.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Invalid aircraft metadata field: {key}")
        result[field] = value.strip() or None if isinstance(value, str) else None
    flags = item.get("dbFlags")
    if flags is not None and (not isinstance(flags, int) or isinstance(flags, bool) or flags < 0):
        raise ValueError("Invalid aircraft metadata field: dbFlags")
    result["db_flags"] = flags
    return result
