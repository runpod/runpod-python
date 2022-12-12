'''
PodWorker | modules | inference.py
Interacts with the model to make predictions.
'''

import os
import zipfile

# -------------------------- Import Model Predictors ------------------------- #
try:
    from predict import Predictor as SD  # pylint: disable=E0401
except ImportError:
    print('Stable Diffusion model not found, using Dreambooth instead.')

try:
    from predictor import Predictor as DB  # pylint: disable=E0401
except ImportError:
    print('Dreambooth model not found, using Stable Diffusion instead.')

from .download import download_input_objects
from .logging import log


model_name = os.environ.get('MODEL', 'stable_diffusion')

if model_name == 'stable_diffusion':
    cog_predictor = SD()
    cog_predictor.setup()
    log('Stable Diffusion model loaded.')

if model_name == 'dreambooth':
    cog_predictor = DB()
    cog_predictor.setup()
    log('Dreambooth model loaded.')


class Models:
    ''' Interface for the model.'''

    def run(self, model_inputs):
        '''
        Predicts the output of the model.
        '''
        if model_name == 'stable_diffusion':
            return self.stable_diffusion(model_inputs)

        if model_name == 'dreambooth':
            return self.dreambooth(model_inputs)

        return None

    def stable_diffusion(self, model_inputs):
        '''
        Runs the stable diffusion model on the given inputs.
        '''
        job_results = cog_predictor.predict(
            prompt=model_inputs["prompt"],
            width=model_inputs.get('width', 512),
            height=model_inputs.get('height', 512),
            init_image=model_inputs.get('init_image', None),
            mask=model_inputs.get('mask', None),
            prompt_strength=model_inputs.get('prompt_strength', 0.8),
            num_outputs=model_inputs.get('num_outputs', 1),
            num_inference_steps=model_inputs.get('num_inference_steps', 50),
            guidance_scale=model_inputs.get('guidance_scale', 7.5),
            scheduler=model_inputs.get('scheduler', "K-LMS"),
            seed=model_inputs.get('seed', None)
        )

        return job_results

    def dreambooth(self, model_inputs):
        '''
        Runs the dreambooth model on the given inputs.
        Returns the location of newly trained model weights.
        '''
        model_inputs["instance_data"] = download_input_objects(model_inputs["instance_data"])[0]

        job_results = cog_predictor.predict(
            instance_prompt=model_inputs["instance_prompt"],
            class_prompt=model_inputs["class_prompt"],
            instance_data=model_inputs["instance_data"],
            class_data=model_inputs.get("class_data", None),
            num_class_images=model_inputs.get("num_class_images", 50),
            save_sample_prompt=model_inputs.get("save_sample_prompt", None),
            save_sample_negative_prompt=model_inputs.get("save_sample_negative_prompt", None),
            n_save_sample=model_inputs.get("n_save_sample", 1),
            save_guidance_scale=model_inputs.get("save_guidance_scale", 7.5),
            save_infer_steps=model_inputs.get("save_infer_steps", 50),
            pad_tokens=model_inputs.get("pad_tokens", False),
            with_prior_preservation=model_inputs.get("with_prior_preservation", True),
            prior_loss_weight=model_inputs.get("prior_loss_weight", 1.0),
            seed=model_inputs.get("seed", 512),
            resolution=model_inputs.get("resolution", 512),
            center_crop=model_inputs.get("center_crop", False),
            train_text_encoder=model_inputs.get("train_text_encoder", True),
            train_batch_size=model_inputs.get("train_batch_size", 1),
            sample_batch_size=model_inputs.get("sample_batch_size", 2),
            num_train_epochs=model_inputs.get("num_train_epochs", 1),
            max_train_steps=model_inputs.get("max_train_steps", 2000),
            gradient_accumulation_steps=model_inputs.get("gradient_accumulation_steps", 1),
            gradient_checkpointing=model_inputs.get("gradient_checkpointing", False),
            learning_rate=model_inputs.get("learning_rate", 1e-6),
            scale_lr=model_inputs.get("scale_lr", False),
            lr_scheduler=model_inputs.get("lr_scheduler", "constant"),
            lr_warmup_steps=model_inputs.get("lr_warmup_steps", 0),
            use_8bit_adam=model_inputs.get("use_8bit_adam", True),
            adam_beta1=model_inputs.get("adam_beta1", 0.9),
            adam_beta2=model_inputs.get("adam_beta2", 0.999),
            adam_weight_decay=model_inputs.get("adam_weight_decay", 1e-2),
            adam_epsilon=model_inputs.get("adam_epsilon", 1e-8),
            max_grad_norm=model_inputs.get("max_grad_norm", 1.0),
        )

        os.makedirs("output_objects", exist_ok=True)

        with zipfile.ZipFile(job_results, 'r') as zip_ref:
            zip_ref.extractall("output_objects")

        job_results = []
        for file in os.listdir("output_objects/samples"):
            job_results.append(os.path.join("output_objects/samples", file))

        return job_results
