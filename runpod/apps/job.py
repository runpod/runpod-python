"""submitted queue jobs shared by decorated handles and Queue stubs."""

from typing import Any, AsyncIterator, Dict, TYPE_CHECKING

from .invoker import Invoker, StreamInvoker
from .targets import FINAL_STATUSES, DEFAULT_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from .targets import InvocationTarget


class Job:
    """a submitted queue job.

    job operations are sync by default and expose async forms through
    ``.aio``:

        job.status()
        await job.status.aio()
        job.result()
        job.cancel()
        job.retry()

    generator jobs stream partial outputs:

        for chunk in job.stream(): ...
        async for chunk in job.stream.aio(): ...
    """

    def __init__(self, data: Dict[str, Any], target: "InvocationTarget"):
        self._data = dict(data)
        self._target = target
        self.status = Invoker(self._status_async)
        self.result = Invoker(self._result_async)
        self.output = self.result
        self.cancel = Invoker(self._cancel_async)
        self.retry = Invoker(self._retry_async)
        self.stream = StreamInvoker(self._stream_async)

    @property
    def id(self) -> str:
        return self._data.get("id", "")

    @property
    def done(self) -> bool:
        return self._data.get("status", "UNKNOWN") in FINAL_STATUSES

    def _update(self, data: Dict[str, Any]) -> None:
        self._data.update(data)

    async def _status_async(self) -> str:
        # terminal statuses never change; skip the network round trip
        if not self.done:
            self._update(await self._target.job_status(self.id))
        return self._data.get("status", "UNKNOWN")

    async def _result_async(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
        return await self._target.wait(
            self._data,
            timeout=timeout,
            on_status=self._update,
        )

    async def _cancel_async(self) -> "Job":
        self._update(await self._target.cancel_job(self.id))
        return self

    async def _retry_async(self) -> "Job":
        self._update(await self._target.retry_job(self.id))
        return self

    async def _stream_async(
        self, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> AsyncIterator[Any]:
        async for chunk in self._target.stream_job(self.id, timeout=timeout):
            yield chunk

    def __repr__(self) -> str:
        status = self._data.get("status", "UNKNOWN")
        return f"Job(id={self.id!r}, status={status!r})"
