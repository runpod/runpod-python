"""shared name-or-id resolution for named platform resources."""

from typing import Any, Callable, Dict, List, Optional


def find_by_id_or_name(
    items: List[Dict[str, Any]],
    ref: str,
    *,
    noun: str,
    error: Callable[[str], Exception],
) -> Optional[Dict[str, Any]]:
    """find the item whose id equals ref, else the unique name match.

    returns None when nothing matches. raises error(message) when a
    name matches more than one item, since the id is then needed to
    disambiguate.
    """
    for item in items:
        if item["id"] == ref:
            return item
    matches = [item for item in items if item.get("name") == ref]
    if len(matches) > 1:
        ids = ", ".join(match["id"] for match in matches)
        raise error(f"multiple {noun} named '{ref}' ({ids}); reference by id")
    return matches[0] if matches else None
