"""errors raised by the apps surface."""

from yarl import URL


class AppError(Exception):
    """base error for the apps sdk."""


class AppHTTPError(AppError):
    """an http failure with a sanitized request url and response excerpt."""

    def __init__(self, method: str, url: str, status: int, body_excerpt: str = ""):
        self.method = method
        self.url = str(URL(url).with_user(None).with_query(None).with_fragment(None))
        self.status = status
        self.body_excerpt = body_excerpt
        detail = f": {body_excerpt}" if body_excerpt else ""
        super().__init__(f"HTTP {status} ({method} {self.url}){detail}")


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


class JobStatusError(AppHTTPError):
    """a job-status request failed with an http error."""

    def __init__(
        self, job_id: str, status: int, method: str, url: str, body_excerpt: str = ""
    ):
        self.job_id = job_id
        super().__init__(method, url, status, body_excerpt)
        self.args = (f"could not retrieve status for job '{job_id}': {self.args[0]}",)


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
