'''
RunPod | API | Mutations | User
'''

def generate_user_mutation(pubkey):
    ''''
    Generates a mutation to edit a user.
    '''
    input_fields = []

    input_fields.append(f'pubKey: "{pubkey}"')

    # Format input fields
    input_string = ", ".join(input_fields)

    return f"""
    mutation {{
        updateUserSettings(
            input: {{
                {input_string}
            }}
        ) {{
            id
            pubKey
        }}
    }}
    """
