"""the @schedule decorator: records a cron expression on a handle.

schedules only take effect through `rp deploy` (the schedule must live
server-side). recurring schedule execution is gated on backend support;
deploying a scheduled resource raises ScheduleNotSupported until it lands.
"""

from typing import Callable, TypeVar

T = TypeVar("T")

SCHEDULE_ATTR = "__runpod_schedule__"

# flipped when the backend ships recurring schedule triggers
SCHEDULES_ENABLED = False


def schedule(*, cron: str) -> Callable[[T], T]:
    """attach a cron schedule to a queue or task handle.

        @app.task(name="hourly", gpu=GpuType.NVIDIA_GEFORCE_RTX_4090)
        @schedule(cron="0 * * * *")
        async def hourly_job(): ...

    decorator order is flexible: @schedule can sit above or below the
    app decorator. the cron string is validated at deploy time.
    """
    if not cron or not isinstance(cron, str):
        raise ValueError("cron must be a non-empty string")

    def decorator(target: T) -> T:
        # works on both raw functions (before app decorator) and handles
        # (after), since handles expose their spec and raw fns get stamped
        spec = getattr(target, "spec", None)
        if spec is not None:
            spec.schedule = cron
        else:
            setattr(target, SCHEDULE_ATTR, cron)
        return target

    return decorator
