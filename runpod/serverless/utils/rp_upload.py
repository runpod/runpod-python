''' PodWorker | modules | upload.py '''
# pylint: disable=too-many-arguments

import os
import io
import time
import uuid
import shutil
import logging
import threading
import multiprocessing
from urllib.parse import urlparse
from typing import Optional, Tuple

import boto3
from boto3 import session
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from tqdm_loggable.auto import tqdm


logger = logging.getLogger("runpod upload utility")
FMT = "%(filename)-20s:%(lineno)-4d %(asctime)s %(message)s"
logging.basicConfig(level=logging.INFO, format=FMT, handlers=[logging.StreamHandler()])


def extract_region_from_url(endpoint_url):
    """
    Extracts the region from the endpoint URL.
    """
    parsed_url = urlparse(endpoint_url)
    # AWS/backblaze S3-like URL
    if '.s3.' in endpoint_url:
        return endpoint_url.split('.s3.')[1].split('.')[0]

    # DigitalOcean Spaces-like URL
    if parsed_url.netloc.endswith('.digitaloceanspaces.com'):
        return endpoint_url.split('.')[1].split('.digitaloceanspaces.com')[0]

    return None


# --------------------------- S3 Bucket Connection --------------------------- #
def get_boto_client(
        bucket_creds: Optional[dict] = None) -> Tuple[boto3.client, TransferConfig]:  # pragma: no cover # pylint: disable=line-too-long
    '''
    Returns a boto3 client and transfer config for the bucket.
    '''
    bucket_session = session.Session()

    boto_config = Config(
        signature_version='s3v4',
        retries={
            'max_attempts': 3,
            'mode': 'standard'
        }
    )

    transfer_config = TransferConfig(
        multipart_threshold=1024 * 25,
        max_concurrency=multiprocessing.cpu_count(),
        multipart_chunksize=1024 * 25,
        use_threads=True
    )

    if bucket_creds:
        endpoint_url = bucket_creds['endpointUrl']
        access_key_id = bucket_creds['accessId']
        secret_access_key = bucket_creds['accessSecret']
    else:
        endpoint_url = os.environ.get('BUCKET_ENDPOINT_URL', None)
        access_key_id = os.environ.get('BUCKET_ACCESS_KEY_ID', None)
        secret_access_key = os.environ.get('BUCKET_SECRET_ACCESS_KEY', None)

    if endpoint_url and access_key_id and secret_access_key:
        # Extract region from the endpoint URL
        region = extract_region_from_url(endpoint_url)

        boto_client = bucket_session.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=boto_config,
            region_name=region
        )
    else:
        boto_client = None

    return boto_client, transfer_config


# ---------------------------------------------------------------------------- #
#                                 Upload Image                                 #
# ---------------------------------------------------------------------------- #
def upload_image(job_id, image_location, result_index=0, results_list=None):  # pragma: no cover
    '''
    Upload a single file to bucket storage.
    '''
    image_name = str(uuid.uuid4())[:8]
    boto_client, _ = get_boto_client()
    file_extension = os.path.splitext(image_location)[1]
    content_type = "image/" + file_extension.lstrip(".")

    with open(image_location, "rb") as input_file:
        output = input_file.read()

    if boto_client is None:
        # Save the output to a file
        print("No bucket endpoint set, saving to disk folder 'simulated_uploaded'")
        print("If this is a live endpoint, please reference the following:")
        print("https://github.com/runpod/runpod-python/blob/main/docs/serverless/utils/rp_upload.md")  # pylint: disable=line-too-long

        os.makedirs("simulated_uploaded", exist_ok=True)
        sim_upload_location = f"simulated_uploaded/{image_name}{file_extension}"

        with open(sim_upload_location, "wb") as file_output:
            file_output.write(output)

        if results_list is not None:
            results_list[result_index] = sim_upload_location

        return sim_upload_location

    bucket = time.strftime('%m-%y')
    boto_client.put_object(
        Bucket=f'{bucket}',
        Key=f'{job_id}/{image_name}{file_extension}',
        Body=output,
        ContentType=content_type
    )

    presigned_url = boto_client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': f'{bucket}',
            'Key': f'{job_id}/{image_name}{file_extension}'
        }, ExpiresIn=604800)

    if results_list is not None:
        results_list[result_index] = presigned_url

    return presigned_url


# ---------------------------------------------------------------------------- #
#                                Files To Upload                               #
# ---------------------------------------------------------------------------- #
def files(job_id, file_list):  # pragma: no cover
    '''
    Uploads a list of files in parallel.
    Once all files are uploaded, the function returns the presigned URLs list.
    '''
    upload_progress = []  # List of threads
    file_urls = [None] * len(file_list)  # Resulting list of URLs for each file

    for index, selected_file in enumerate(file_list):
        new_upload = threading.Thread(
            target=upload_image,
            args=(job_id, selected_file, index, file_urls)
        )

        new_upload.start()
        upload_progress.append(new_upload)

    # Wait for all uploads to finish
    for upload in upload_progress:
        upload.join()

    return file_urls


# --------------------------- Custom Bucket Upload --------------------------- #
def bucket_upload(job_id, file_list, bucket_creds):  # pragma: no cover
    '''
    Uploads files to bucket storage.
    '''
    temp_bucket_session = session.Session()

    temp_boto_config = Config(
        signature_version='s3v4',
        retries={
            'max_attempts': 3,
            'mode': 'standard'
        }
    )

    temp_boto_client = temp_bucket_session.client(
        's3',
        endpoint_url=bucket_creds['endpointUrl'],
        aws_access_key_id=bucket_creds['accessId'],
        aws_secret_access_key=bucket_creds['accessSecret'],
        config=temp_boto_config
    )

    bucket_urls = []

    for selected_file in file_list:
        with open(selected_file, 'rb') as file_data:
            temp_boto_client.put_object(
                Bucket=str(bucket_creds['bucketName']),
                Key=f'{job_id}/{selected_file}',
                Body=file_data,
            )

        bucket_urls.append(
            f"{bucket_creds['endpointUrl']}/{bucket_creds['bucketName']}/{job_id}/{selected_file}")

    return bucket_urls


# ------------------------- Single File Bucket Upload ------------------------ #
def upload_file_to_bucket(
        file_name: str, file_location: str,
        bucket_creds: Optional[dict] = None,
        bucket_name: Optional[str] = None,
        prefix: Optional[str] = None,
        extra_args: Optional[dict] = None
) -> str:  # pragma: no cover
    '''
    Uploads a single file to bucket storage and returns a presigned URL.
    '''
    boto_client, transfer_config = get_boto_client(bucket_creds)

    if not bucket_name:
        bucket_name = time.strftime('%m-%y')

    key = f"{prefix}/{file_name}" if prefix else file_name

    if boto_client is None:
        print("No bucket endpoint set, saving to disk folder 'local_upload'")
        print("If this is a live endpoint, please reference the following:")
        print("https://github.com/runpod/runpod-python/blob/main/docs/serverless/utils/rp_upload.md")  # pylint: disable=line-too-long

        os.makedirs("local_upload", exist_ok=True)
        local_upload_location = f"local_upload/{file_name}"
        shutil.copyfile(file_location, local_upload_location)

        return local_upload_location

    file_size = os.path.getsize(file_location)
    with tqdm(total=file_size, unit='B', unit_scale=True, desc=file_name) as progress_bar:
        upload_file_args = {
            "Filename": file_location,
            "Bucket": bucket_name,
            "Key": key,
            "Config": transfer_config,
            "Callback": progress_bar.update
        }

        if extra_args:
            upload_file_args["ExtraArgs"] = extra_args

        boto_client.upload_file(**upload_file_args)

    presigned_url = boto_client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': bucket_name,
            'Key': key
        }, ExpiresIn=604800)

    return presigned_url


# --------------------------- Upload Memory Object --------------------------- #
def upload_in_memory_object(
        file_name: str, file_data: bytes,
        bucket_creds: Optional[dict] = None,
        bucket_name: Optional[str] = None,
        prefix: Optional[str] = None) -> str:  # pragma: no cover
    '''
    Uploads an in-memory object (bytes) to bucket storage and returns a presigned URL.
    '''
    boto_client, transfer_config = get_boto_client(bucket_creds)

    if not bucket_name:
        bucket_name = time.strftime('%m-%y')

    key = f"{prefix}/{file_name}" if prefix else file_name

    file_size = len(file_data)
    with tqdm(total=file_size, unit='B', unit_scale=True, desc=file_name) as progress_bar:
        boto_client.upload_fileobj(
            io.BytesIO(file_data), bucket_name, key,
            Config=transfer_config,
            Callback=progress_bar.update
        )

    presigned_url = boto_client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': bucket_name,
            'Key': key
        }, ExpiresIn=604800)

    return presigned_url
