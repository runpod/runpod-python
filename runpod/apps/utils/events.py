"""duck-typed event sink dispatch."""

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def emit(sink: Optional[object], event: str, *args: Any) -> None:
    """invoke the named handler on the sink if it exists.

    handler failures are swallowed and logged: a broken event
    renderer must never break the operation it observes.
    """
    handler = getattr(sink, event, None)
    if handler is not None:
        try:
            handler(*args)
        except Exception:  # noqa: BLE001 - rendering must never break calls
            log.debug("event sink %s failed", event, exc_info=True)
