# The Serverless Worker



## Worker Refresh

For more complex operations where you are downloading files or making changes to the worker it can be beneficial to refresh the worker between jobs. This can be accomplished by enabling a refresh_worker worker flag in 1 of two ways:

   1. Enable on start with `runpod.serverless.start({"handler": handler, "refresh_worker": True})`, this will refresh the worker after every job return, even if the handler raises an error.
   2. Return `refresh_worker=True` as a top level dictionary key in the handler return. This can selectively be used to refresh the worker based on the job return.
