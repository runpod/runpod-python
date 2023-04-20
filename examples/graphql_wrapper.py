''''
GraphQL wrapper for the RunPod API
'''

import time
import runpod


runpod.api_key = "YOUR_RUNPOD_API_KEY"

# Get all GPUs
gpus = runpod.get_gpus()
print(gpus)

# Get a specific GPU
gpu = runpod.get_gpu("NVIDIA GeForce RTX 3070")
print(gpu)

# Create a pod
pod = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")
print(pod)

# Pause while the pod is being created
print("Waiting for pod to be created...")
time.sleep(10)

# Stop a pod
pod = runpod.stop_pod(pod["id"])
print(pod)

# Pause while the pod is being stopped
print("Waiting for pod to be stopped...")
time.sleep(10)

# Resume a pod
pod = runpod.resume_pod(pod["id"], 1)
print(pod)

# Pause while the pod is being resumed
print("Waiting for pod to be resumed...")
time.sleep(10)

# Terminate a pod
runpod.terminate_pod(pod["id"])
