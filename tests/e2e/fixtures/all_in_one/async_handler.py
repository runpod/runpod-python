from runpod_flash import Endpoint


@Endpoint(name="async-worker", cpu="cpu3c-1-2")
async def async_handler(input_data: dict) -> dict:
    return {"input_received": input_data, "status": "ok"}
