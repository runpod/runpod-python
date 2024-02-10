""" Test suite for the generate_container_registry_auth function. """

import unittest

from runpod.api.mutations.container_register_auth import (
    generate_container_registry_auth,
)


class TestGenerateContainerRegistryAuth(unittest.TestCase):
    """ Test suite for the generate_container_registry_auth function. """

    def test_generate_container_registry_auth(self):
        """
        Test that the generate_container_registry_auth function produces the correct
        GraphQL mutation string with the provided name, username, and password.
        """
        # Define test inputs
        name = "testRegistry"
        username = "testUser"
        password = "testPass"

        # Generate the actual mutation string
        actual_mutation = generate_container_registry_auth(
            name, username, password
        ).strip()

        # Verify key components of the mutation string
        self.assertIn("mutation SaveRegistryAuth", actual_mutation)
        self.assertIn(
            'saveRegistryAuth(input: {name: "testRegistry", username: "testUser", password: "testPass"})',  # pylint: disable=line-too-long
            actual_mutation,
        )
        self.assertIn("id", actual_mutation)
        self.assertIn("name", actual_mutation)
