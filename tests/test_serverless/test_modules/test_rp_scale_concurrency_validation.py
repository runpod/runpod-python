import asyncio
from unittest import TestCase

from runpod.serverless.modules.rp_scale import JobScaler


class TestJobScalerConcurrencyValidation(TestCase):
    def test_concurrency_modifier_none_defaults_to_one(self):
        scaler = JobScaler({"concurrency_modifier": lambda _: None})
        asyncio.run(scaler.set_scale())
        self.assertEqual(scaler.current_concurrency, 1)

    def test_concurrency_modifier_zero_defaults_to_one(self):
        scaler = JobScaler({"concurrency_modifier": lambda _: 0})
        asyncio.run(scaler.set_scale())
        self.assertEqual(scaler.current_concurrency, 1)

    def test_concurrency_modifier_negative_defaults_to_one(self):
        scaler = JobScaler({"concurrency_modifier": lambda _: -3})
        asyncio.run(scaler.set_scale())
        self.assertEqual(scaler.current_concurrency, 1)

    def test_concurrency_modifier_valid_int_is_applied(self):
        scaler = JobScaler({"concurrency_modifier": lambda _: 4})
        asyncio.run(scaler.set_scale())
        self.assertEqual(scaler.current_concurrency, 4)

