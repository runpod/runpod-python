'''
runpod | serverless | utils | validator.py
Provides a function to validate the input to the model.
'''
# pylint: disable=too-many-branches


def validate(raw_input, schema):
    '''
    Validates the input.
    Checks to see if the provided inputs match the expected types.
    Checks to see if the required inputs are included.
    Sets the default values for the inputs that are not provided.
    Validates the inputs using the lambda constraints.

    Returns either the list of errors or a validated_job_input.
    {"errors": ["error1", "error2"]}
    or
    {"validated_input": {"input1": "value1", "input2": "value2"}
    '''
    error_list = []

    # Check for unexpected inputs.
    for key in raw_input:
        if key not in schema:
            error_list.append(f"Unexpected input. {key} is not a valid input option.")

    # Checks for missing required inputs or sets the default values.
    for key, rules in schema.items():
        if 'required' not in rules:
            error_list.append(f"Schema error, missing 'required' for {key}.")
        elif rules['required'] and key not in raw_input:
            error_list.append(f"{key} is a required input.")
        elif rules['required'] and key not in raw_input and "default" not in rules:
            error_list.append(f"Schema error, missing default value for {key}.")
        elif not rules['required'] and key not in raw_input and "default" not in rules:
            error_list.append(f"Schema error, missing default value for {key}.")
        elif not rules['required'] and key not in raw_input:
            raw_input[key] = raw_input.get(key, rules['default'])

    for key, rules in schema.items():
        # Enforce floats to be floats.
        if rules['type'] is float and type(raw_input[key]) in [int, float]:
            raw_input[key] = float(raw_input[key])

        # Check for the correct type.
        if not isinstance(raw_input[key], rules['type']) and raw_input[key] is not None:
            error_list.append(f"{key} should be {rules['type']} type, not {type(raw_input[key])}.")

        # Check lambda constraints.
        if "constraints" in rules:
            if not rules['constraints'](raw_input[key]):
                error_list.append(f"{key} does not meet the constraints.")

    validation_return = {"validated_input": raw_input}
    if error_list:
        validation_return = {"errors": error_list}

    return validation_return
