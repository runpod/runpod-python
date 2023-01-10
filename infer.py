'''
Template for the infer.py file.
Input -> model parameters
Output -> list of files
'''
# pylint: disable=unused-argument,too-few-public-methods

import runpod


def validator():
    '''
    Optional validator function.
    Lists the expected inputs of the model, and their types.
    If there are any conflicts the job request is errored out.
    '''
    return {
        'prompt': {
            'type': str,
            'required': True
        }
    }


def run(model_inputs):
    '''
    Predicts the output of the model.
    Returns output path, with the seed used to generate the image.

    If errors are encountered, return a dictionary with the key "error".
    The error can be a string or list of strings.
    '''

    # Return Errors
    # return {"error": "Error Message"}

    return [
        {
            "image": "/path/to/image.png",
            "seed": "1234"
        }
    ]


runpod.serverless.start({"handler": run})
