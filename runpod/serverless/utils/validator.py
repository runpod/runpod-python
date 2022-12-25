'''
runpod | serverless | utils | validator.py
Provides a function to validate the input to the model.
'''


def validate(job_input, validation_source):
    '''
    Validates the input.
    Checks to see if the provided inputs match the expected types.
    Checks to see if the required inputs are included.
    '''
    input_errors = []

    for key, value in job_input.items():
        if key not in validation_source:
            input_errors.append(f"Unexpected input. {key} is not a valid input option.")

    for key, value in validation_source.items():
        if value['required'] and key not in job_input:
            input_errors.append(f"{key} is a required input.")

        if key in job_input and not isinstance(job_input[key], value['type']):
            input_errors.append(
                f"{key} should be {value['type']} type, not {type(job_input[key])}.")

    return input_errors
