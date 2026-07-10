"""graphql mutations for the apps control-plane client."""

MUTATION_SAVE_ENDPOINT = """
mutation saveEndpoint($input: EndpointInput!) {
    saveEndpoint(input: $input) {
        id
        name
        templateId
        gpuIds
        instanceIds
        workersMin
        workersMax
        idleTimeout
        aiKey
    }
}
"""

MUTATION_DELETE_ENDPOINT = """
mutation deleteEndpoint($id: String!) {
    deleteEndpoint(id: $id)
}
"""

MUTATION_DEPLOY_CPU_POD = """
mutation deployCpuPod($input: deployCpuPodInput!) {
    deployCpuPod(input: $input) { id desiredStatus }
}
"""

MUTATION_DEPLOY_POD = """
mutation deployPod($input: PodFindAndDeployOnDemandInput) {
    podFindAndDeployOnDemand(input: $input) { id desiredStatus }
}
"""

MUTATION_TERMINATE_POD = """
mutation terminatePod($input: PodTerminateInput!) {
    podTerminate(input: $input)
}
"""

MUTATION_CREATE_FLASH_APP = """
mutation createFlashApp($input: CreateFlashAppInput!) {
    createFlashApp(input: $input) { id name }
}
"""

MUTATION_CREATE_FLASH_ENVIRONMENT = """
mutation createFlashEnvironment($input: CreateFlashEnvironmentInput!) {
    createFlashEnvironment(input: $input) { id name }
}
"""

MUTATION_CREATE_NETWORK_VOLUME = """
mutation createNetworkVolume($input: CreateNetworkVolumeInput!) {
    createNetworkVolume(input: $input) {
        id
        name
        size
        dataCenterId
    }
}
"""

MUTATION_SAVE_REGISTRY_AUTH = """
mutation SaveRegistryAuth($input: SaveRegistryAuthInput!) {
    saveRegistryAuth(input: $input) { id name }
}
"""

MUTATION_DELETE_REGISTRY_AUTH = """
mutation DeleteRegistryAuth($registryAuthId: String!) {
    deleteRegistryAuth(registryAuthId: $registryAuthId)
}
"""

MUTATION_CREATE_SECRET = """
mutation secretCreate($input: SecretCreateInput!) {
    secretCreate(input: $input) { id name }
}
"""

MUTATION_DELETE_SECRET = """
mutation secretDelete($id: ID!) {
    secretDelete(id: $id)
}
"""

MUTATION_DELETE_FLASH_APP = """
mutation deleteFlashApp($flashAppId: String!) {
    deleteFlashApp(flashAppId: $flashAppId)
}
"""

MUTATION_DELETE_FLASH_ENVIRONMENT = """
mutation deleteFlashEnvironment($flashEnvironmentId: String!) {
    deleteFlashEnvironment(flashEnvironmentId: $flashEnvironmentId)
}
"""

MUTATION_CREATE_FLASH_AUTH_REQUEST = """
mutation createFlashAuthRequest {
    createFlashAuthRequest {
        id
        status
        expiresAt
    }
}
"""

MUTATION_PREPARE_ARTIFACT_UPLOAD = """
mutation PrepareArtifactUpload($input: PrepareFlashArtifactUploadInput!) {
    prepareFlashArtifactUpload(input: $input) {
        uploadUrl
        objectKey
        expiresAt
    }
}
"""

MUTATION_FINALIZE_ARTIFACT_UPLOAD = """
mutation FinalizeArtifactUpload($input: FinalizeFlashArtifactUploadInput!) {
    finalizeFlashArtifactUpload(input: $input) { id manifest }
}
"""

MUTATION_DEPLOY_BUILD = """
mutation deployBuildToEnvironment($input: DeployBuildToEnvironmentInput!) {
    deployBuildToEnvironment(input: $input) { id name }
}
"""
