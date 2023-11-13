""" GraphQL queries for endpoints. """

QUERY_ENDPOINT = """
query Query {
  myself {
    endpoints {
      aiKey
      gpuIds
      id
      idleTimeout
      name
      networkVolumeId
      locations
      scalerType
      scalerValue
      templateId
      type
      userId
      version
      workersMax
      workersMin
      workersStandby
      gpuCount
      env {
        key
        value
      }
      createdAt
      networkVolume {
        id
        dataCenterId
      }
    }
  }
}
"""
