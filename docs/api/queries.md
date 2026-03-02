## get_gpus

```python
import runpod

runpod.api_key = "your_runpod_api_key"

gpus = runpod.get_gpus()

for gpu in gpus:
    print(gpu)
```

### get_gpus Output

```json
{'id': 'NVIDIA A100 80GB PCIe', 'displayName': 'A100 80GB', 'memoryInGb': 80}
{'id': 'NVIDIA A100-SXM4-80GB', 'displayName': 'A100 SXM 80GB', 'memoryInGb': 80}
{'id': 'NVIDIA A30', 'displayName': 'A30', 'memoryInGb': 24}
```

## get_gpu

```python
gpu_id = "NVIDIA A100 80GB PCIe"
gpu = runpod.get_gpu(gpu_id)

print(gpu)
```

### get_gpu Output

```json
{'id': 'NVIDIA A100 80GB PCIe', 'displayName': 'A100 80GB', 'memoryInGb': 80, 'secureCloud': True, 'communityCloud': True, 'lowestPrice': {'minimumBidPrice': 1.158, 'uninterruptablePrice': 1.89}}
```

## get_pod_logs

```python
import runpod

runpod.api_key = "your_runpod_api_key"

result = runpod.get_pod_logs("POD_ID", tail=50)
print(result["logs"])
```

### get_pod_logs Output

```json
{"pod_id": "abc123", "logs": "2024-01-15 10:30:00 Starting server...\n2024-01-15 10:30:01 Server ready.", "source": "stdout"}
```
