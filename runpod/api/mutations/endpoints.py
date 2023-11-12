""" RunPod | API Wrapper | Mutations | Endpoints """

# pylint: disable=too-many-arguments


def generate_endpoint_mutation(
    name: str, template_id: str, gpu_ids: str = "AMPERE_16",
    network_volume_id: str = None, locations: str = None,
    idle_timeout: int = 5, scaler_type: str = "QUEUE_DELAY", scaler_value: int = 4,
    workers_min: int = 0, workers_max: int = 3
):
    """ Generate a string for a GraphQL mutation to create a new endpoint. """
    input_fields = []

    # ------------------------------ Required Fields ----------------------------- #
    input_fields.append(f'name: "{name}"')
    input_fields.append(f'templateId: "{template_id}"')
    input_fields.append(f'gpuIds: "{gpu_ids}"')

    # ------------------------------ Optional Fields ----------------------------- #
    if network_volume_id is not None:
        input_fields.append(f'networkVolumeId: "{network_volume_id}"')
    else:
        input_fields.append('networkVolumeId: ""')

    if locations is not None:
        input_fields.append(f'locations: "{locations}"')
    else:
        input_fields.append('locations: ""')

    input_fields.append(f'idleTimeout: {idle_timeout}')
    input_fields.append(f'scalerType: "{scaler_type}"')
    input_fields.append(f'scalerValue: {scaler_value}')
    input_fields.append(f'workersMin: {workers_min}')
    input_fields.append(f'workersMax: {workers_max}')

    # Format the input fields into a string
    input_fields_string = ", ".join(input_fields)

    return f"""
    mutation {{
        saveEndpoint(
            input: {{
                {input_fields_string}
            }}
        ) {{
            id
            name
            templateId
            gpuIds
            networkVolumeId
            locations
            idleTimeout
            scalerType
            scalerValue
            workersMin
            workersMax
        }}
    }}
    """


def update_endpoint_template_mutation(
    endpoint_id: str, template_id: str
):
    """ Generate a string for a GraphQL mutation to update an existing endpoint's template. """
    input_fields = []

    # ------------------------------ Required Fields ----------------------------- #
    input_fields.append(f'templateId: "{template_id}"')
    input_fields.append(f'endpointId: "{endpoint_id}"')

    # Format the input fields into a string
    input_fields_string = ", ".join(input_fields)
    result = f"""
    mutation {{
        updateEndpointTemplate(input: {{{input_fields_string}}}) {{
            id
            templateId
        }}
    }}
    """
    return result
