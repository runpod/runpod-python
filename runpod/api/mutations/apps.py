"""GraphQL mutations for capabilities absent from the resource REST API."""

# endpoint saves retain flash bindings, cached models, and schedules atomically.
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

# task provisioning requires terminateAfter and supportPublicIp.
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
