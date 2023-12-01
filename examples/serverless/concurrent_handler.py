""" Concurrency Modifier Example """

import runpod


# ---------------------------------- Handler --------------------------------- #
async def async_generator_handler(job):
    '''
    Async generator type handler.
    '''
    return job


# --------------------------- Concurrency Modifier --------------------------- #
def concurrency_modifier(current_concurrency=1):
    '''
    Concurrency modifier.
    '''
    desired_concurrency = current_concurrency

    # Do some logic to determine the desired concurrency.

    return desired_concurrency


runpod.serverless.start({
    "handler": async_generator_handler,
    "concurrency_modifier": concurrency_modifier
})
