"""
RunPod | API Wrapper | Queries | GPUs
"""

QUERY_GPU_TYPES = """
query GpuTypes {
  gpuTypes {
    id
    displayName
    memoryInGb
  }
}
"""


def generate_gpu_query(gpu_id):
    '''
    Generate a query for a specific GPU type
    '''

    return f"""
    query GpuTypes {{
      gpuTypes(input: {{id: "{gpu_id}"}}) {{
        id
        displayName
        memoryInGb
        secureCloud
        communityCloud
        lowestPrice(input: {{gpuCount: 1}}) {{
          minimumBidPrice
          uninterruptablePrice
        }}
      }}
    }}
    """
