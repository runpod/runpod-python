'''
PodWorker | modules | inference.py
Interacts with the model to make predictions.
'''

# -------------------------- Import Model Predictors ------------------------- #
import infer

from .logging import log


class Model:
    ''' Interface for the model.'''

    def __init__(self):
        '''
        Loads the model.
        '''
        log('Model loaded.')

    def input_validation(self, model_inputs):
        '''
        Validates the input.
        Checks to see if the provided inputs match the expected types.
        Checks to see if the required inputs are included.
        '''
        log("Validating inputs.")
        if not hasattr(infer, 'validator'):
            log("No input validation function found. Skipping validation.")
            return []

        input_validations = infer.validator()
        input_errors = []

        log("Checking for required inputs.")
        for key, value in input_validations.items():
            if value['required'] and key not in model_inputs:
                input_errors.append(f"{key} is a required input.")

        log("Checking for unexpected inputs and input types.")
        for key, value in model_inputs.items():
            if key not in input_validations:
                input_errors.append(f"Unexpected input. {key} is not a valid input option.")

            if not isinstance(value, input_validations[key]['type']):
                input_errors.append(
                    f"{key} should be {input_validations[key]['type']} type, not {type(value)}.")

        return input_errors

    def run(self, job):
        '''
        Predicts the output of the model.
        '''
        input_errors = self.input_validation(job['input'])
        if input_errors:
            return [
                {
                    "error": input_errors
                }
            ]

        return infer.run(job)
