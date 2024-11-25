"""Utility function for transforming HuggingFace repositories into model-cache paths"""


def resolve_model_cache_path_from_hugginface_repository(
    huggingface_repository: str,
    /,
    path_template: str = "/runpod/cache/{model}/{revision}",  # TODO: Should we just hardcode this?
) -> str:
    """
    Resolves the path to cache a HuggingFace model based on its repository string.

    Args:
        huggingface_repository (str): Repository string in format "model_name:revision" or
                                    "org/model_name:revision". If no revision is specified,
                                    "main" is used.
        path_template (str, optional): Template string for the cache path. Defaults to
                                     "/runpod/cache/{model}/{revision}".

    Returns:
        str: Absolute path where the model should be cached, following the template
             specified in path_template.

    Examples:
        >>> resolve_model_cache_path_from_hugginface_repository("stable-diffusion-v1-5:main")
        "/runpod/cache/stable-diffusion-v1-5/main"
        >>> resolve_model_cache_path_from_hugginface_repository("runwayml/stable-diffusion-v1-5")
        "/runpod/cache/runwayml/stable-diffusion-v1-5/main"
    """
    model, *revision = huggingface_repository.rsplit(":", 1)
    return path_template.format(
        model=model, revision=revision[0] if revision else "main"
    )
