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
        '''
        input_types = self.predictor.inputs(model_inputs)

        for key, value in model_inputs.items():
            if key not in input_types:
                raise ValueError(f'Input {key} not expected.')

            if not isinstance(value, input_types[key]):
                raise ValueError(f'Input {key} should be {input_types[key]}.')

    def run(self, model_inputs):
        '''
        Predicts the output of the model.
        '''
        return self.predictor.run(model_inputs)
