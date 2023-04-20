# Serverless Testing

To test your serverless worker locally, create the file `test_input.json` in the root directory of your project. This file will be used to test your worker locally.

```json
{
  "input": {
    "your_model_input_key": "your_model_input_value"
  }
}
```

Then run the python file which contains your worker handler, it will be called with the contents of `test_input.json` as the input.
