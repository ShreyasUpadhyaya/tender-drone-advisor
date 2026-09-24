from typing import Any

SUPPORTED_UNITS = {"kg", "g", "m", "s", "W", "V", "Ah", "count", "paise"}

# Category JSON is intentionally constrained. Measurement-key suffixes are the
# canonical internal SI unit; source units remain explicit provenance metadata.
SPEC_TYPES: dict[str, dict[str, type | tuple[type, ...]]] = {
    "platform": {
        "mtow_kg": (int, float),
        "payload_kg": (int, float),
        "range_m": (int, float),
        "interfaces": list,
    },
    "airframe": {"mtow_kg": (int, float), "payload_kg": (int, float), "interfaces": list},
    "battery": {"voltage_v": (int, float), "cells": int, "capacity_c": (int, float)},
    "motor": {"max_current_a": (int, float), "voltage_v": (int, float), "paired_esc_sku": str},
    "esc": {"max_current_a": (int, float), "voltage_v": (int, float)},
    "propeller": {"diameter_m": (int, float)},
    "camera": {"payload_kg": (int, float), "interface": str, "sensor": str},
    "communication": {"range_m": (int, float), "interface": str},
    "flight_controller": {"interface": str},
    "navigation": {"interface": str, "constellation": str},
    "accessory": {"interface": str},
    "gcs_software": {"protocol": str},
    "certification": {"standard": str},
}


def validate_item_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    errors = []
    for field in ("weight_grams", "cost_paise", "inventory_qty", "lead_time_days"):
        if payload.get(field, 0) < 0:
            errors.append({"field": field, "code": "negative_value"})
    specs = payload.get("specs", {})
    for key, value in specs.items():
        expected = SPEC_TYPES.get(payload.get("category"), {}).get(key)
        if expected is None:
            errors.append({"field": f"specs.{key}", "code": "unsupported_specification"})
        elif not isinstance(value, expected) or isinstance(value, bool):
            errors.append({"field": f"specs.{key}", "code": "invalid_specification_type"})
        elif isinstance(value, (int, float)) and value < 0:
            errors.append({"field": f"specs.{key}", "code": "negative_value"})
    for field in ("weight_unit", "capacity_unit", "voltage_unit"):
        if specs.get(field) and specs[field] not in SUPPORTED_UNITS:
            errors.append({"field": f"specs.{field}", "code": "unsupported_unit"})
    for low, high in (("min_voltage_v", "max_voltage_v"), ("min_capacity_ah", "max_capacity_ah")):
        if specs.get(low) is not None and specs.get(high) is not None and specs[low] > specs[high]:
            errors.append({"field": low, "code": "invalid_range"})
    if payload.get("availability") == "in_stock" and payload.get("inventory_qty", 0) == 0:
        errors.append({"field": "availability", "code": "inventory_mismatch"})
    return errors
