"""GraphQL queries for capabilities absent from the resource REST API."""

# rest stock filters require a power-of-two count of at least two.
QUERY_CPU_STOCK = """
query CpuStock($cpuFlavorInput: CpuFlavorInput, $specificsInput: SpecificsInput) {
    cpuFlavors(input: $cpuFlavorInput) {
        specifics(input: $specificsInput) { stockStatus }
    }
}
"""

QUERY_FLASH_APP_BY_NAME = """
query getFlashAppByName($flashAppName: String!) {
    flashAppByName(flashAppName: $flashAppName) {
        id
        name
        flashEnvironments {
            id
            name
            state
            activeBuildId
            endpoints { id name }
        }
    }
}
"""

QUERY_SECRETS = """
query mySecrets {
    myself {
        secrets { id name description createdAt }
    }
}
"""

QUERY_FLASH_APPS = """
query getFlashApps {
    myself {
        flashApps {
            id
            name
            flashEnvironments {
                id
                name
                state
                createdAt
                activeBuildId
            }
            flashBuilds { id createdAt }
        }
    }
}
"""

QUERY_FLASH_ENVIRONMENT_BY_NAME = """
query getFlashEnvironmentByName($input: FlashEnvironmentByNameInput!) {
    flashEnvironmentByName(input: $input) {
        id
        name
        state
        activeBuildId
        createdAt
        endpoints { id name }
        networkVolumes { id name }
    }
}
"""

QUERY_FLASH_AUTH_REQUEST_STATUS = """
query flashAuthRequestStatus($flashAuthRequestId: String!) {
    flashAuthRequestStatus(flashAuthRequestId: $flashAuthRequestId) {
        id
        status
        expiresAt
        apiKey
    }
}
"""
