# Infer File

The RunPod worker will interact with your model through the `infer.py` file located in the root directory. This file is responsible for loading the model, performing input validation, and running inference on the input data. The structure of the `infer.py` file is as follows:

```python
class Predictor:
    def setup(self):
        # Load model
        self.model = ...

    def inputs(self, model_inputs):
        # Validate inputs
        input_class_types = {
            "input_name": str,
        }
        return ...

    def run(self, model_inputs):
        # Run inference
        return ...
```

## setup

The `setup` method is called once when the worker is initialized. This is where you should load your model. The `setup` method takes no arguments and returns nothing.

## inputs
