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

    def validator(self):
        '''
        Lists the expected inputs of the model, and their types.
        '''
        return {
            'prompt': {
                'type': str,
                'required': True
            }
        }

    def run(self, model_inputs):
        '''
        Predicts the output of the model.
        Returns output path, with the seed used to generate the image.
        '''
        return {"image": "/path/to/image.png", "seed": "1234"}
