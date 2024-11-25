import unittest

from runpod.serverless.utils.rp_model_cache import (
    resolve_model_cache_path_from_hugginface_repository,
)


class TestModelCache(unittest.TestCase):
    """Tests for rp_model_cache"""

    def test_with_revision(self):
        """Test with a revision"""
        path = resolve_model_cache_path_from_hugginface_repository(
            "runwayml/stable-diffusion-v1-5:experimental"
        )
        self.assertEqual(
            path, "/runpod/cache/runwayml/stable-diffusion-v1-5/experimental"
        )

    def test_without_revision(self):
        """Test without a revision"""
        path = resolve_model_cache_path_from_hugginface_repository(
            "runwayml/stable-diffusion-v1-5"
        )
        self.assertEqual(path, "/runpod/cache/runwayml/stable-diffusion-v1-5/main")

    def test_with_multiple_colons(self):
        """Test with multiple colons"""
        path = resolve_model_cache_path_from_hugginface_repository(
            "runwayml/stable-diffusion:v1-5:experimental"
        )
        self.assertEqual(
            path, "/runpod/cache/runwayml/stable-diffusion:v1-5/experimental"
        )

    def test_with_custom_path_template(self):
        """Test with a custom path template"""
        path = resolve_model_cache_path_from_hugginface_repository(
            "runwayml/stable-diffusion-v1-5:experimental",
            "/my-custom-model-cache/{model}/{revision}",
        )
        self.assertEqual(
            path, "/my-custom-model-cache/runwayml/stable-diffusion-v1-5/experimental"
        )

    def test_with_missing_model_name(self):
        """Test with a missing model name"""
        path = resolve_model_cache_path_from_hugginface_repository(":experimental")
        self.assertIsNone(path)


if __name__ == "__main__":
    unittest.main()
