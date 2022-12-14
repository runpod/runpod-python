'''
Template for the infer.py file.
Input -> model parameters
Output -> list of files
'''


class Predictor:
    ''' Interface for the model. '''

    def setup(self):
        ''' Loads the model. '''

    def run(self, model_inputs):
        '''
        Predicts the output of the model.
        '''
        return None
