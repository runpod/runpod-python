"""errors raised by the apps surface."""

from yarl import URL


class AppError(Exception):
    """base error for the apps sdk."""


class EndpointNotFound(AppError):
    """a resource was invoked but is not deployed."""

    def __init__(self, app_name: str, resource_name: str):
        self.app_name = app_name
        self.resource_name = resource_name
        super().__init__(
            f"'{resource_name}' in app '{app_name}' is not deployed. "
            f"run `rp flash deploy` first, or use .local() to run it here."
        )


class RemoteExecutionError(AppError):
    """the remote worker reported a failure executing the function."""


class JobStatusError(AppError):
    """a job-status request failed with an http error."""

    def __init__(self, job_id: str, status: int, method: str, url: str):
        self.job_id = job_id
        self.status = status
        self.method = method
        self.url = str(URL(url).with_user(None).with_query(None).with_fragment(None))
        super().__init__(
            f"could not retrieve status for job '{job_id}': "
            f"HTTP {status} ({method} {self.url})"
        )


class ScheduleNotSupported(AppError):
    """@schedule requires backend support that is not yet available."""

    def __init__(self) -> None:
        super().__init__(
            "recurring schedules are not yet supported by the runpod api. "
            "the @schedule decorator records intent but cannot be deployed."
        )


class InvalidResourceError(AppError):
    """a decorator was applied to an unsupported target or with bad config."""


class EndpointReconciliationError(AppError):
    """endpoint reconciliation failed with rollback results and remaining changes."""
