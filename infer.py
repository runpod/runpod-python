'''
Template for the infer.py file.
Input -> model parameters
Output -> list of files
'''
# pylint: disable=unused-argument,too-few-public-methods


class Predictor:
    ''' Interface for the model. '''

    def setup(self):
        ''' Loads the model. '''

    def inputs(self, model_inputs):
        '''
        Lists the expected inputs of the model, and their types.
        '''
        inputs = {
            'prompt': str,
        }
        return inputs

    def run(self, model_inputs):
        '''
        Predicts the output of the model.
        Returns output path, with the seed used to generate the image.
        '''
        return {"image": "/path/to/image.png", "seed": "1234"}
