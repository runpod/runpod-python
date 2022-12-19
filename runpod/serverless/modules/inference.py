'''
PodWorker | modules | inference.py
Interacts with the model to make predictions.
'''

# -------------------------- Import Model Predictors ------------------------- #
from infer import Predictor

from .logging import log


class Model:
    ''' Interface for the model.'''

    def __init__(self):
        '''
        Loads the model.
        '''
        self.predictor = Predictor()
        self.predictor.setup()
        log('Model loaded.')

    def input_validation(self, model_inputs):
        '''
        Validates the input.
        Checks to see if the provided inputs match the expected types.
        Checks to see if the required inputs are included.
        '''
        log("Validating inputs.")
        input_validations = self.predictor.validator()

        input_errors = []

        for requirement in input_validations:
            if requirement['required'] and requirement not in model_inputs:
                input_errors.append(f"{requirement} is a required input.")

        for key, value in model_inputs.items():
            if key not in input_validations:
                input_errors.append(f"Unexpected input. {key} is not a valid input option.")

            if not isinstance(value, input_validations[key]['type']):
                input_errors.append(
                    f"{key} should be {input_validations[key]['type']} type, not {type(value)}.")

        return input_errors

    def run(self, model_inputs):
        '''
        Predicts the output of the model.
        '''
        return self.predictor.run(model_inputs)
