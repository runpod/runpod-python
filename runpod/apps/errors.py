"""errors raised by the apps surface."""


class AppError(Exception):
    """base error for the apps sdk."""


class EndpointNotFound(AppError):
    """a resource was invoked but is not deployed."""

    def __init__(self, app_name: str, resource_name: str):
        self.app_name = app_name
        self.resource_name = resource_name
        super().__init__(
            f"'{resource_name}' in app '{app_name}' is not deployed. "
            f"run `rp deploy` first, or use .local() to run it here."
        )


class RemoteExecutionError(AppError):
    """the remote worker reported a failure executing the function."""


class ScheduleNotSupported(AppError):
    """@schedule requires backend support that is not yet available."""

    def __init__(self) -> None:
        super().__init__(
            "recurring schedules are not yet supported by the runpod api. "
            "the @schedule decorator records intent but cannot be deployed."
        )


class InvalidResourceError(AppError):
    """a decorator was applied to an unsupported target or with bad config."""
