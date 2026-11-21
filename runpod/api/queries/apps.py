"""GraphQL queries for capabilities absent from the resource REST API."""

# rest stock filters require a power-of-two count of at least two.
QUERY_CPU_STOCK = """
query CpuStock($cpuFlavorInput: CpuFlavorInput, $specificsInput: SpecificsInput) {
    cpuFlavors(input: $cpuFlavorInput) {
        specifics(input: $specificsInput) { stockStatus }
    }
}
"""

QUERY_ENDPOINT_SNAPSHOT = """
query EndpointSnapshot($id: String!) {
    myself {
        endpoint(id: $id) {
            id name type instanceIds gpuIds gpuCount locations
            workersMin workersMax workersPFBTarget idleTimeout
            scalerType scalerValue executionTimeoutMs workerInitTimeoutMs
            flashBootType minCudaVersion allowedCudaVersions
            modelReferences env { key value }
            networkVolumeIds { networkVolumeId }
            volumes { volumeId volumeType }
            template {
                name imageName containerDiskInGb dockerArgs
                env { key value }
                ports containerRegistryAuthId advancedStart startScript config
            }
        }
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
            endpoints { id name template { name } }
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
        endpoints { id name template { name } }
        networkVolumes { id name }
    }
}
"""

# no apiKey: the console shows the key only to the approver.
QUERY_FLASH_AUTH_REQUEST_STATUS = """
query flashAuthRequestStatus($flashAuthRequestId: String!) {
    flashAuthRequestStatus(flashAuthRequestId: $flashAuthRequestId) {
        id
        status
    }
}
"""
