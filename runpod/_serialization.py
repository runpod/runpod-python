"""finite json validation and wire-value conversion for callers and workers."""

import json
from typing import Any


def _json_text(value: Any, label: str) -> str:
    try:
        return json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise TypeError(f"{label} must be json-serializable: {exc}") from exc


def validate_json(value: Any, label: str = "value") -> None:
    """require values encodable as finite json."""
    _json_text(value, label)


def normalize_json(value: Any, label: str = "value") -> Any:
    """apply json's container and key conversions without a network roundtrip."""
    return json.loads(_json_text(value, label))
