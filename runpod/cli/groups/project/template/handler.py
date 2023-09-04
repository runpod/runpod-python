''' A template for a handler file. '''

import runpod

def handler(job):
    '''
    This is the handler function for the job.
    '''
    job_input = job['input']
    example_name = job_input.get('example_name', 'World')
    return f"Hello, {example_name}!"

runpod.serverless.start({"handler": handler})
