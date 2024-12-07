"""Utility function for transforming HuggingFace repositories into model-cache paths"""

import typing
from runpod.serverless.modules.rp_logger import RunPodLogger

log = RunPodLogger()


def resolve_model_cache_path_from_hugginface_repository(
    huggingface_repository: str,
    /,
    path_template: str = "/runpod/cache/{model}/{revision}",  # TODO: Should we just hardcode this?
) -> typing.Union[str, None]:
    """
    Resolves the model-cache path for a HuggingFace model based on its repository string.

    Args:
        huggingface_repository (str): Repository string in format "model_name:revision" or
                                    "org/model_name:revision". If no revision is specified,
                                    "main" is used. For example:
                                    - "runwayml/stable-diffusion-v1-5:experimental"
                                    - "runwayml/stable-diffusion-v1-5" (uses "main" revision)
                                    - "stable-diffusion-v1-5:main"
        path_template (str, optional): Template string for the cache path. Must contain {model}
                                     and {revision} placeholders. Defaults to "/runpod/cache/{model}/{revision}".

    Returns:
        str | None: Absolute path where the model is cached, following the template provided in path_template. Returns None if no model name could be extracted.

    Examples:
        >>> resolve_model_cache_path_from_hugginface_repository("runwayml/stable-diffusion-v1-5:experimental")
        "/runpod/cache/runwayml/stable-diffusion-v1-5/experimental"
        >>> resolve_model_cache_path_from_hugginface_repository("runwayml/stable-diffusion-v1-5")
        "/runpod/cache/runwayml/stable-diffusion-v1-5/main"
        >>> resolve_model_cache_path_from_hugginface_repository(":experimental")
        None
    """
    model, *revision = huggingface_repository.rsplit(":", 1)
    if not model:
        # We could throw an exception here but returning None allows us to filter a list of repositories without needing a try/except block
        log.warn(  # type: ignore in strict mode the typechecker complains about this method being partially unknown
            f'Unable to resolve the model-cache path for "{huggingface_repository}"'
        )
        return None
    return path_template.format(
        model=model, revision=revision[0] if revision else "main"
    )
