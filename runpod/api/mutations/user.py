'''
RunPod | API | Mutations | User
'''

def generate_user_mutation(pubkey):
    ''''
    Generates a mutation to edit a user.
    '''
    input_fields = []

    escaped_pubkey = pubkey.replace('\n', '\\n')
    input_fields.append(f'pubKey: "{escaped_pubkey}"')

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
