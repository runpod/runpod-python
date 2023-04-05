'''
This is the starting point for your serverless API.
It can be named anything you want, but needs to have the following:

- import runpod
- start function

To return an error, return a dictionary with the key "error" and the value being the error message.
'''

import runpod  # Required


def is_even(job):
    '''
    Example function that returns True if the input is even, False otherwise.

    "job_input" will contain the input that was passed to the API along with some other metadata.
    The structure will look like this:
    {
        "id": "some-id",
        "input": {"number": 2}
    }

    Whatever is returned from this function will be returned to the user as the output.
    '''

    job_input = job["input"]
    the_number = job_input["number"]

    if not isinstance(the_number, int):
        return {"error": "Silly human, you need to pass an integer."}

    if the_number % 2 == 0:
        return True

    return False


if __name__ == "__main__":
    runpod.serverless.start({"handler": is_even})
