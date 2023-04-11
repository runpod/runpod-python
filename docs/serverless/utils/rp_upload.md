## S3 Uploader

.env variables:

```bash
# S3 Bucket
BUCKET_ENDPOINT_URL =  # S3 bucket endpoint url
BUCKET_ACCESS_KEY_ID =  # S3 bucket access key id
BUCKET_SECRET_ACCESS_KEY =  # S3 bucket secret access key
```

*Note: The upload utilitiy utilizes the Virtual-hosted-style URL with the bucket name in the host name. For example, `https: // bucket-name.s3.amazonaws.com`.*

![RunPod Template Location](.docs/images/../../../../images/env_var_location.png)

## Examples

These examples demonstrate how to use the provided functions to upload a file and an in-memory object to the bucket storage. They show how to set up the bucket credentials, define the file name and location (or data), and call the respective function to upload and obtain a presigned URL for the uploaded object.

### upload_file_to_bucket

```python
from runpod.serverless.utils import upload_file_to_bucket

# Define your bucket credentials
bucket_creds = {
    'endpointUrl': 'https://your-bucket-endpoint-url.com',
    'accessId': 'your_access_key_id',
    'accessSecret': 'your_secret_access_key',
    'bucketName': 'your_bucket_name'
}

# Define the file name and file location
file_name = 'example.txt'
file_location = '/path/to/your/local/file/example.txt'

# Upload the file and get the presigned URL
presigned_url = upload_file_to_bucket(file_name, file_location, bucket_creds)

# Print the presigned URL
print(f"Presigned URL: {presigned_url}")
```

### upload_in_memory_object

```python
from runpod.serverless.utils import upload_in_memory_object

# Define your bucket credentials
bucket_creds = {
    'endpointUrl': 'https://your-bucket-endpoint-url.com',
    'accessId': 'your_access_key_id',
    'accessSecret': 'your_secret_access_key',
    'bucketName': 'your_bucket_name'
}

# Define the file name and file data (bytes)
file_name = 'example.txt'
file_data = b'This is an example text.'

# Upload the in-memory object and get the presigned URL
presigned_url = upload_in_memory_object(file_name, file_data, bucket_creds)

# Print the presigned URL
print(f"Presigned URL: {presigned_url}")
```
