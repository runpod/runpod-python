## Validator Utility (rp_validator.py)

The validator utility allows you to define the expected inputs for your worker and validate them against the input data. If any errors are found, they will be returned and the worker will not run. If no errors are found, the function will return the validated input, including any default values for inputs not provided.

The validate function takes in two arguments: the first is the input data and the second is the schema to validate against. If you have nested input data, you will need to define separate schemas for each layer and call the validator on them individually.

The schema is a nested dictionary that defines the validation rules for each input. For each input, you can define the following rules:

- `required` (defaults `False`) - If the input is required or not (true/false)
- `default` (defaults `None`) - A default value to use if the input is not provided.
- `type` (required) - The type of the input
- `constraints` (optional) - A lambda function that takes in the input and returns true or false

## Example Usage

```python
from runpod.serverless.utils.rp_validator import validate

# Define the schema for the expected inputs
schema = {
    "input1": {
        "type": str,    # expected type
        "required": True,    # is input required
    },
    "input2": {
        "type": int,    # expected type
        "required": False,    # is input required
        "default": 10,    # default value if input isn't provided
        "constraints": lambda x: x > 0,    # constraints on the value of the input
    }
}

# Define the raw input to be validated
raw_input = {
    "input1": "hello",
    "input2": 15,
}

# Call the validate function
result = validate(raw_input, schema)

# Check if there were any errors
if 'errors' in result:
    print(f"Errors: {result['errors']}")
else:
    print(f"Validated input: {result['validated_input']}")
```

This Python script will validate the input against the defined schema, printing any errors if they exist, or the validated input if there are no errors.
