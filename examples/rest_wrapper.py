"""REST v2 wrapper for the Runpod API."""

import time

import runpod

runpod.api_key = "YOUR_RUNPOD_API_KEY"

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

print("Waiting for pod to be created...")
time.sleep(10)

pod = runpod.stop_pod(pod["id"])
print(pod)

print("Waiting for pod to be stopped...")
time.sleep(10)

pod = runpod.resume_pod(pod["id"])
print(pod)

print("Waiting for pod to be resumed...")
time.sleep(10)

runpod.terminate_pod(pod["id"])
