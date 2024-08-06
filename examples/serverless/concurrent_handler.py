""" Concurrency Modifier Example """

import runpod


# ---------------------------------- Handler --------------------------------- #
async def async_generator_handler(job):
    '''
    Async generator type handler.
    '''
    return job


runpod.serverless.start({
    "handler": async_generator_handler,
})
