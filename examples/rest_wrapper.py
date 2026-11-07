"""Create, stop, resume, and terminate a pod with the Runpod API.

This creates a billable GPU pod and terminates it at the end.
"""

import time

import runpod

# Run `rp login` once to store your API key, or uncomment the line below:
# runpod.api_key = "YOUR_RUNPOD_API_KEY"

gpus = runpod.get_gpus()
print(gpus)

gpu = runpod.get_gpu("NVIDIA GeForce RTX 4090")
print(gpu)

pod = runpod.create_pod(
    "test",
    "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
    "NVIDIA GeForce RTX 4090",
)
print(pod)
pod_id = pod["id"]

print("Waiting for pod to be created...")
time.sleep(10)

print(runpod.stop_pod(pod_id))

print("Waiting for pod to be stopped...")
time.sleep(10)

print(runpod.resume_pod(pod_id))

print("Waiting for pod to be resumed...")
time.sleep(10)

runpod.terminate_pod(pod_id)
