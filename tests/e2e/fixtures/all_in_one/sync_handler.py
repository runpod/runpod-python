from runpod_flash import Endpoint

from e2e_template import get_e2e_template


@Endpoint(name="sync-worker", cpu="cpu3c-1-2", template=get_e2e_template())
def sync_handler(input_data: dict) -> dict:
    return {"input_received": input_data, "status": "ok"}
