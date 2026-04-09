#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <cuda_runtime.h>
#include <nvml.h>
#include <sys/utsname.h>

void log_linux_kernel_version() {
    struct utsname buffer;
    if (uname(&buffer) == 0) {
        printf("Linux Kernel Version: %s\n", buffer.release);
    } else {
        perror("uname");
    }
}

void log_cuda_driver_version() {
    int driver_version;
    cudaError_t result = cudaDriverGetVersion(&driver_version);
    if (result == cudaSuccess) {
        printf("CUDA Driver Version: %d.%d\n", driver_version / 1000, (driver_version % 1000) / 10);
    } else {
        printf("Failed to get CUDA driver version. Error code: %d (%s)\n", result, cudaGetErrorString(result));
    }
}

void enumerate_gpus_and_test() {
    nvmlReturn_t result;
    result = nvmlInit();
    if (result != NVML_SUCCESS) {
        printf("Failed to initialize NVML: %s\n", nvmlErrorString(result));
        return;
    }

    unsigned int device_count;
    result = nvmlDeviceGetCount(&device_count);
    if (result != NVML_SUCCESS) {
        printf("Failed to get GPU count: %s\n", nvmlErrorString(result));
        nvmlShutdown();
        return;
    }

    printf("Found %u GPUs:\n", device_count);
    for (unsigned int i = 0; i < device_count; i++) {
        nvmlDevice_t device;
        char name[NVML_DEVICE_NAME_BUFFER_SIZE];
        char uuid[NVML_DEVICE_UUID_BUFFER_SIZE];
        result = nvmlDeviceGetHandleByIndex(i, &device);
        if (result == NVML_SUCCESS) {
            nvmlDeviceGetName(device, name, sizeof(name));
            nvmlDeviceGetUUID(device, uuid, sizeof(uuid));
            printf("GPU %u: %s (UUID: %s)\n", i, name, uuid);

            // Allocate memory on GPU to test accessibility
            cudaSetDevice(i);
            float *d_tensor;
            cudaError_t cuda_result = cudaMalloc((void**)&d_tensor, sizeof(float) * 10);
            if (cuda_result == cudaSuccess) {
                printf("GPU %u memory allocation test passed.\n", i);
                cudaFree(d_tensor);
            } else {
                printf("GPU %u memory allocation test failed. Error code: %d (%s)\n", i, cuda_result, cudaGetErrorString(cuda_result));
            }
        } else {
            printf("Failed to get handle for GPU %u: %s (Error code: %d)\n", i, nvmlErrorString(result), result);
        }
    }

    nvmlShutdown();
}

int main() {
    log_linux_kernel_version();
    log_cuda_driver_version();
    enumerate_gpus_and_test();
    return 0;
}
