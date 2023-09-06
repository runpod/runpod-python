from transformers import HfApi

SELECTED_MODEL = "<<MODEL_NAME>>"

def get_model_framework(model_name):
    api = HfApi()
    model_files = api.model_info(model_name).files

    # Check the files associated with the model
    if "pytorch_model.bin" in model_files:
        return "PyTorch"
    elif "tf_model.h5" in model_files:
        return "TensorFlow"
    else:
        return "Unknown"
