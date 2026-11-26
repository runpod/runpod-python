# Serverless Testing

It is important to test your serverless workers locally before deploying them to the cloud. This will help you catch errors early and avoid wasting cloud resources. This guide will show you how to test your serverless workers locally.

If any errors are returned by the worker while running a test_input job the worker will exit with a non-zero exit code. Otherwise, the worker will exit with a zero exit code. This can be used to check if the worker ran successfully, for example in a CI/CD pipeline.

## Test Input File

To test your serverless worker locally, create the file `test_input.json` in the root directory of your project. This file will be used to test your worker locally.

```json
{
  "input": {
    "your_model_input_key": "your_model_input_value"
  }
}
```

Then run the python file which contains your worker handler, it will be called with the contents of `test_input.json` as the input.

To use a different file, for example to keep one input file per test case, pass its path with `--test_file`:

```bash
python your_handler.py --test_file tests/inputs/long_prompt.json
```

`--test-file` works as well. If both `--test_input` and `--test_file` are passed, `--test_input` is used.

## Test Input Argument

The second method of testing your serverless worker locally is to pass the input as an argument to the python file which contains your worker handler.

```bash
python your_handler.py --test_input '{"input": {"your_model_input_key": "your_model_input_value"}}'
```
