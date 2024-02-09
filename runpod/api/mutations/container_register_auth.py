""" RunPod | API Wrapper | Mutations | Container Registry Auth """


def generate_container_registry_auth(name: str, username: str, password: str):
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
