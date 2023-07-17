'''
runpod | serverless | utils | validator.py
Provides a function to validate the input to the model.
'''
# pylint: disable=too-many-branches

import json
from typing import Any, Dict, List, Union

# Error messages
UNEXPECTED_INPUT_ERROR = "Unexpected input. {} is not a valid input option."
MISSING_REQUIRED_ERROR = "{} is a required input."
MISSING_DEFAULT_ERROR = "Schema error, missing default value for {}."
MISSING_TYPE_ERROR = "Schema error, missing type for {}."
INVALID_TYPE_ERROR = "{} should be {} type, not {}."
CONSTRAINTS_ERROR = "{} does not meet the constraints."
SCHEMA_ERROR = "Schema error, {} is not a dictionary."


def _add_error(error_list: List[str], message: str) -> None:
    error_list.append(message)

def _check_for_unexpected_inputs(raw_input, schema, error_list):
    for key in raw_input:
        if key not in schema:
            _add_error(error_list, UNEXPECTED_INPUT_ERROR.format(key))


def _validate_and_transform_schema_items(schema, error_list):
    for key, rules in schema.items():
        if not isinstance(rules, dict):
            try:
                schema[key] = json.loads(rules)
            except json.decoder.JSONDecodeError:
                _add_error(error_list, SCHEMA_ERROR.format(key))


def _validate_required_inputs_and_set_defaults(raw_input, schema, validated_input, error_list):
    for key, rules in schema.items():
        if 'type' not in rules:
            _add_error(error_list, MISSING_TYPE_ERROR.format(key))

        if 'required' not in rules:
            _add_error(error_list, MISSING_REQUIRED_ERROR.format(key))
        elif rules['required'] and key not in raw_input:
            _add_error(error_list, MISSING_REQUIRED_ERROR.format(key))
        elif not rules['required'] and key not in raw_input:
            if "default" in rules:
                validated_input[key] = rules['default']
            else:
                _add_error(error_list, MISSING_DEFAULT_ERROR.format(key))


def _validate_input_against_schema(schema, validated_input, error_list):
    for key, rules in schema.items():
        if key in validated_input:
            # Enforce floats to be floats.
            try:
                if rules['type'] is float and type(validated_input[key]) in [int, float]:
                    validated_input[key] = float(validated_input[key])
            except TypeError:
                continue

            # Check for the correct type.
            is_instance = isinstance(validated_input[key], rules['type'])
            if validated_input[key] is not None and not is_instance:
                _add_error(
                    error_list,
                    f"{key} should be {rules['type']} type, not {type(validated_input[key])}."
                )

        # Check lambda constraints.
        if "constraints" in rules and not rules['constraints'](validated_input.get(key)):
            _add_error(error_list, CONSTRAINTS_ERROR.format(key))

def validate(
    raw_input: Dict[str, Any], schema: Dict[str, Any]
) -> Dict[str, Union[Dict[str, Any], List[str]]]:
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
    validated_input = raw_input.copy()

    # Separate the process into functions for better readability
    _check_for_unexpected_inputs(raw_input, schema, error_list)
    _validate_and_transform_schema_items(schema, error_list)
    _validate_required_inputs_and_set_defaults(raw_input, schema, validated_input, error_list)
    _validate_input_against_schema(schema, validated_input, error_list)

    validation_return = {"validated_input": validated_input}
    if error_list:
        validation_return = {"errors": error_list}

    return validation_return
