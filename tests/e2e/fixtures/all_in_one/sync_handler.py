from runpod_flash import Endpoint


@Endpoint(name="sync-worker", cpu="cpu3c-1-2")
def sync_handler(input_data: dict) -> dict:
    return {"input_received": input_data, "status": "ok"}
