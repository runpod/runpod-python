'''
Template for the infer.py file.
Input -> model perameters
Output -> list of files
'''


class Predictor:
    ''' Interface for the model. '''

    def setup(self):
        ''' Loads the model. '''

    def predict(self, model_inputs):
        '''
        Predicts the output of the model.
        '''
        return None
