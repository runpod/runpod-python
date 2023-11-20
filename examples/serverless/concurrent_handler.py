# import runpod


# async def async_generator_handler(job):
#     '''
#     Async generator type handler.
#     '''
#     return


# max_number_of_jobs_allowed = 10


# vllm_in_queue = 3  # Function


# def concurrency_controller(desired_concurrency=1):  # get desired concurrency
#     '''
#     Concurrency controller.
#     '''
#     # max_number_of_jobs_allowed = 1

#     # if num_of_running_jobs < 1:
#     #     max_number_of_jobs_allowed = max_number_of_jobs_allowed

#     # if num_of_running_jobs >= 1:
#     #     max_number_of_jobs_allowed = max_number_of_jobs_allowed

#     if vllm_in_queue < 10:
#         max_number_of_jobs_allowed = max_number_of_jobs_allowed + 1

#     if vllm_in_queue >= 10:
#         max_number_of_jobs_allowed = max_number_of_jobs_allowed - 1

#     return max_number_of_jobs_allowed


# def concurrency_modifier(current_concurrency=1):
#     '''
#     Concurrency modifier.
#     '''

#     desired_concurrency = current_concurrency

#     return desired_concurrency


# runpod.serverless.start({
#     "handler": async_generator_handler,
#     "concurrency_controller": concurrency_controller
# })
