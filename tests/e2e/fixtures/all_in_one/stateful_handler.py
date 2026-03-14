from typing import Optional

from runpod_flash import Endpoint

state = {}


@Endpoint(name="stateful-worker", cpu="cpu3c-1-2")
def stateful_handler(action: str, key: str, value: Optional[str] = None) -> dict:
    if action == "set":
        state[key] = value
        return {"stored": True}
    elif action == "get":
        return {"value": state.get(key)}
    return {"error": "unknown action"}
