from runpod_flash import Endpoint


@Endpoint(name="cold-start-worker", cpu="cpu3c-1-2")
def handler(input_data: dict) -> dict:
    return {"status": "ok"}
