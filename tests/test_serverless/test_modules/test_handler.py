""" Unit tests for the handler module.
"""

import unittest

from runpod.serverless.modules.rp_handler import is_generator


class TestIsGenerator(unittest.TestCase):
    """Tests for the is_generator function."""

    def test_callable_non_generator_object(self):
        """Test that a callable object is not a generator."""

        class CallableObject:
            def __call__(self):
                return "I'm a callable object!"

        callable_obj = CallableObject()
        self.assertFalse(is_generator(callable_obj))

    def test_callable_object_generator_object(self):
        """Test that a callable object with a generator method is a generator."""

        class CallableGeneratorObject:
            def __call__(self):
                yield "I'm a callable object with a generator method!"

        callable_obj = CallableGeneratorObject()
        self.assertTrue(is_generator(callable_obj))

    def test_async_callable_non_generator_object(self):
        """Test that an async callable object is not a generator."""

        class AsyncCallableNonGeneratorObject:
            async def __call__(self):
                return "I'm an async callable object!"

        async_callable_obj = AsyncCallableNonGeneratorObject()
        self.assertFalse(is_generator(async_callable_obj))

    def test_async_callable_generator_object(self):
        """Test that an async callable object with a generator method is a generator."""

        class AsyncCallableGeneratorObject:
            async def __call__(self):
                yield "I'm an async callable object with a generator method!"

        async_callable_obj = AsyncCallableGeneratorObject()
        self.assertTrue(is_generator(async_callable_obj))

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
