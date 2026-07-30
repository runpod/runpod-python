"""graphql queries for the apps control-plane client."""

QUERY_MY_ENDPOINTS = """
query myEndpoints {
    myself {
        endpoints {
            id
            name
            templateId
            workersMin
            workersMax
            template { env { key value } }
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
            endpoints { id name }
        }
    }
}
"""

QUERY_GPU_STOCK = """
query GpuStock($gpuTypesInput: GpuTypeFilter, $lowestPriceInput: GpuLowestPriceInput) {
    gpuTypes(input: $gpuTypesInput) {
        lowestPrice(input: $lowestPriceInput) { stockStatus }
    }
}
"""

QUERY_CPU_STOCK = """
query CpuStock($cpuFlavorInput: CpuFlavorInput, $specificsInput: SpecificsInput) {
    cpuFlavors(input: $cpuFlavorInput) {
        specifics(input: $specificsInput) { stockStatus }
    }
}
"""

QUERY_NETWORK_VOLUMES = """
query myVolumes {
    myself {
        networkVolumes { id name size dataCenterId }
    }
}
"""

QUERY_REGISTRY_AUTHS = """
query myRegistryCreds {
    myself {
        containerRegistryCreds { id name }
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
