'''
PodWorker | modules | inference.py
Interacts with the model to make predictions.
'''

# -------------------------- Import Model Predictors ------------------------- #
from infer import Predictor

from .logging import log


class Models:
    ''' Interface for the model.'''

    def __init__(self):
        '''
        Loads the model.
        '''
        self.predictor = Predictor()
        self.predictor.setup()
        log('Model loaded.')

    def run(self, model_inputs):
        '''
        Predicts the output of the model.
        '''
        return self.predictor.run(model_inputs)
