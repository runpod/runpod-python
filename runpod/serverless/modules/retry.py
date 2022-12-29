import random
import asyncio
from functools import wraps


def retry(max_attempts, base_delay, max_delay):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 1
            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt >= max_attempts:
                        raise e

                    # Calculate delay using exponential backoff with random jitter
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    delay *= random.uniform(0.5, 1.5)

                    # Wait for the delay before retrying
                    await asyncio.sleep(delay)
                    attempt += 1
        return wrapper
    return decorator
