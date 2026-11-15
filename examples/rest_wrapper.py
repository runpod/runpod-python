"""Create, stop, resume, and terminate a pod with the Runpod API.

This creates a billable GPU pod. The pod is terminated at the end, including
when a step fails.
"""

import time

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

GPU_TYPE = "NVIDIA GeForce RTX 4090"  # see `rp catalog` for other GPU types
WAIT_SECONDS = 10

gpus = runpod.get_gpus()
print(gpus)

gpu = runpod.get_gpu(GPU_TYPE)
print(gpu)

pod = runpod.create_pod(
    "example-pod",
    "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
    GPU_TYPE,
)
print(pod)
pod_id = pod["id"]

try:
    print("Waiting for pod to be created...")
    time.sleep(WAIT_SECONDS)

    print(runpod.stop_pod(pod_id))

    print("Waiting for pod to be stopped...")
    time.sleep(WAIT_SECONDS)

    # Resuming can fail if no GPU of this type is free on the pod's machine.
    print(runpod.resume_pod(pod_id))

    print("Waiting for pod to be resumed...")
    time.sleep(WAIT_SECONDS)
finally:
    runpod.terminate_pod(pod_id)
    print(f"Terminated pod {pod_id}")
