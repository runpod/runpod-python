'''
runpod | serverless | utils | validator.py
Provides a function to validate the input to the model.
'''


def validate(job_input, schema):
    '''
    Validates the input.
    Checks to see if the provided inputs match the expected types.
    Checks to see if the required inputs are included.
    Sets the default values for the inputs that are not provided.
    Validates the inputs using the lambda constraints.

    Returns either the list of errors or a validated_job_input.
    {"errors": ["error1", "error2"]}
    or
    {"validated_job_input": {"input1": "value1", "input2": "value2"}
    '''
    error_list = []

    # Check for unexpected inputs.
    for key in job_input:
        if key not in schema:
            error_list.append(f"Unexpected input. {key} is not a valid input option.")

    # Checks for missing required inputs or sets the default values.
    for key, rules in schema.items():
        if rules['required'] and key not in job_input:
            error_list.append(f"{key} is a required input.")
        elif key not in job_input:
            job_input[key] = rules['default']

    for key, rules in schema.items():
        # Check for the correct type.
        if not isinstance(job_input[key], rules['type']):
            error_list.append(f"{key} should be {rules['type']} type, not {type(job_input[key])}.")

        # Check lambda constraints.
        if "constraints" in rules:
            if not rules['constraints'](job_input[key]):
                error_list.append(f"{key} does not meet the constraints.")

    if error_list:
        validation_return = {"errors": error_list}
    else:
        validation_return = {"validated_job_input": job_input}

    return validation_return
