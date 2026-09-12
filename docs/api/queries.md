## get_gpus

```python
import runpod

runpod.api_key = "your_runpod_api_key"

gpus = runpod.get_gpus()

for gpu in gpus:
    print(gpu)
```

### get_gpus output

```python
[{
    "id": "NVIDIA GeForce RTX 4090",
    "name": "RTX 4090",
    "pool": "ADA_24",
    "manufacturer": "NVIDIA",
    "memory": 24,
    "secure": True,
    "community": True,
    "price": {"secure": 0.44, "community": 0.31, "serverless": 1.1},
    "maxCount": {"secure": 8, "community": 4},
}]
```

## get_gpu

```python
gpu_id = "NVIDIA GeForce RTX 4090"
gpu = runpod.get_gpu(gpu_id, gpu_quantity=2)

print(gpu)
```

`get_gpu` requests pod availability for the requested GPU count.

### get_gpu output

```python
{
    "id": "NVIDIA GeForce RTX 4090",
    "name": "RTX 4090",
    "memory": 24,
    "availability": "HIGH",
    "dataCenters": [
        {"id": "US-KS-2", "name": "US Kansas 2", "availability": "HIGH"}
    ],
}
```
