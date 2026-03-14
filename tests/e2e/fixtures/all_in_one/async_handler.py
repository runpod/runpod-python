from runpod_flash import Endpoint

from e2e_template import get_e2e_template


@Endpoint(name="async-worker", cpu="cpu3c-1-2", template=get_e2e_template())
async def async_handler(input_data: dict) -> dict:
    return {"input_received": input_data, "status": "ok"}
