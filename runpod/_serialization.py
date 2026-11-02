"""strict json values shared by remote callers and workers."""

import math
from typing import Any


def validate_json(value: Any, label: str = "value") -> None:
    """require finite json primitives, lists, and string-keyed dictionaries."""
    active = set()

    def visit(item: Any, path: str) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise TypeError(f"{path} must be a finite number")
            return
        if not isinstance(item, (list, dict)):
            raise TypeError(f"{path} has unsupported type {type(item).__name__}")
        identity = id(item)
        if identity in active:
            raise TypeError(f"{path} contains a circular reference")
        active.add(identity)
        try:
            if isinstance(item, list):
                for index, child in enumerate(item):
                    visit(child, f"{path}[{index}]")
            else:
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise TypeError(f"{path} must have string keys")
                    visit(child, f"{path}[{key!r}]")
        finally:
            active.remove(identity)

    try:
        visit(value, label)
    except (TypeError, RecursionError) as exc:
        raise TypeError(f"{label} must be json-serializable: {exc}") from exc
