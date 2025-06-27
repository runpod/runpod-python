""" Runpod | API Wrapper | Mutations | Container Registry Auth """


def generate_container_registry_auth(name: str, username: str, password: str):
    """
    Generate a GraphQL mutation string to save container registry authentication details.

    Args:
        name (str): The name of the container registry.
        username (str): The username for authentication.
        password (str): The password for authentication.

    Returns:
        str: The GraphQL mutation string.
    """
    # Prepare the input dictionary
    input_dict = {"name": name, "username": username, "password": password}

    # Convert the input dictionary to a string, properly formatted for GraphQL
    input_str = ", ".join(f'{key}: "{value}"' for key, value in input_dict.items())

    return f"""
    mutation SaveRegistryAuth {{
        saveRegistryAuth(input: {{{input_str}}}) {{
            id
            name
        }}
    }}
    """


def update_container_registry_auth(registry_auth_id: str, username: str, password: str):
    """
    Generate a GraphQL mutation string to update registry authentication details.

    Args:
        registry_auth_id (str): The id of the container registry authentication
        username (str): The username for authentication.
        password (str): The password for authentication.

    Returns:
        str: The GraphQL mutation string.
    """
    # Prepare the input dictionary
    input_dict = {"id": registry_auth_id, "username": username, "password": password}

    # Convert the input dictionary to a string, properly formatted for GraphQL
    input_str = ", ".join(f'{key}: "{value}"' for key, value in input_dict.items())

    return f"""
    mutation UpdateRegistryAuth {{
        updateRegistryAuth(input: {{{input_str}}}) {{
            id
            name
        }}
    }}
    """


def delete_container_registry_auth(registry_auth_id: str):
    """
    Generate a GraphQL mutation string to delete registry authentication details.

    Args:
        registry_auth_id (str): The id of the container registry authentication

    Returns:
        str: The GraphQL mutation string.
    """

    return f"""
    mutation DeleteRegistryAuth {{
        deleteRegistryAuth(registryAuthId: "{registry_auth_id}")
    }}
    """
