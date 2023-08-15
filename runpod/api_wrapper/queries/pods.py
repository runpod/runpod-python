"""
RunPod | API Wrapper | Queries | GPUs
"""

QUERY_POD = """
query myPods {{
    myself {{
        pods {{
            id
            containerDiskInGb
            costPerHr
            desiredStatus
            dockerArgs
            dockerId
            env
            gpuCount
            imageName
            lastStatusChange
            machineId
            memoryInGb
            name
            podType
            port
            ports
            uptimeSeconds
            vcpuCount
            volumeInGb
            volumeMountPath
            machine {{
                gpuDisplayName
            }}
        }}
    }}
}}
"""
