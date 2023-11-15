import runpod


async def async_generator_handler(job):
    '''
    Async generator type handler.
    '''
    return


def concurrency_controller():
    '''
    Concurrency controller.
    '''
    return True


runpod.serverless.start({
    "handler": async_generator_handler,
    "concurrency_controller": concurrency_controller,
    "concurrency_config": {
        "min_concurrent_requests": 2,
        "max_concurrent_requests": 100,
        "concurrency_scale_factor": 4,
        "availability_ratio_threshold": 0.90
    }
})
