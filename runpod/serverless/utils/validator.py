'''
runpod | serverless | utils | validator.py
Provides a function to validate the input to the model.
'''


def validate(input, validation_source):
    '''
    Validates the input.
    Checks to see if the provided inputs match the expected types.
    Checks to see if the required inputs are included.
    '''
    input_errors = []

    for key, value in validation_source.items():
        if value['required'] and key not in input:
            input_errors.append(f"{key} is a required input.")

        if key in input and not isinstance(input[key], value['type']):
            input_errors.append(
                f"{key} should be {value['type']} type, not {type(input[key])}.")

    return input_errors
