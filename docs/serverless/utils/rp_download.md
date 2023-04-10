# Download Utility

The download utility provides functions to download files or objects from given URLs.

## download_files_from_urls Function Examples

In the examples below, replace the job_id and url/urls variables with the appropriate job ID and URLs for the files you want to download. The download_files_from_urls function returns a list of downloaded file absolute paths, which can be used for further processing.

### Download a single file

```python
from runpod.serverless.utils import download_files_from_urls

job_id = "job_123"
url = "https://example.com/file1.txt"
downloaded_files = download_files_from_urls(job_id, url)

print(f"Downloaded file: {downloaded_files[0]}")
```

### Download multiple files

```python
from runpod.serverless.utils import download_files_from_urls

job_id = "job_123"
urls = [
    "https://example.com/file1.txt",
    "https://example.com/file2.png",
    "https://example.com/file3.pdf"
]

downloaded_files = download_files_from_urls(job_id, urls)

for i, file_path in enumerate(downloaded_files):
    print(f"Downloaded file {i + 1}: {file_path}")
```

### Handling invalid URLs

```python
from runpod.serverless.utils import download_files_from_urls

job_id = "job_123"
urls = [
    "https://example.com/file1.txt",
    "https://example.com/non_existent_file.png",
    "https://example.com/file3.pdf"
]

downloaded_files = download_files_from_urls(job_id, urls)

for i, file_path in enumerate(downloaded_files):
    if file_path is not None:
        print(f"Downloaded file {i + 1}: {file_path}")
    else:
        print(f"Failed to download file {i + 1}")
```
