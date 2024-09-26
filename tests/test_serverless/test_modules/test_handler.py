""" Unit tests for the handler module.
"""

import unittest

from runpod.serverless.modules.rp_handler import is_generator


class TestIsGenerator(unittest.TestCase):
    """Tests for the is_generator function."""

    def test_regular_function(self):
        """Test that a regular function is not a generator."""

        def regular_func():
            return "I'm a regular function!"

        self.assertFalse(is_generator(regular_func))

    def test_generator_function(self):
        """Test that a generator function is a generator."""

        def generator_func():
            yield "I'm a generator function!"

        self.assertTrue(is_generator(generator_func))

    def test_async_function(self):
        """Test that an async function is not a generator."""

        async def async_func():
            return "I'm an async function!"

        self.assertFalse(is_generator(async_func))

    def test_async_generator_function(self):
        """Test that an async generator function is a generator."""

        async def async_gen_func():
            yield "I'm an async generator function!"

        self.assertTrue(is_generator(async_gen_func))
