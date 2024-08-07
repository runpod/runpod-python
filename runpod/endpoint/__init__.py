''' Allows endpoints to be imported as a module. '''

from .runner import Endpoint, Job, CustomEndpoint
from .asyncio.asyncio_runner import Endpoint as AsyncioEndpoint
from .asyncio.asyncio_runner import Job as AsyncioJob
