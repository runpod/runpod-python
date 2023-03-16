''' PodWorker | modules | upload.py '''

import os
import time
import uuid
import logging
import threading
from io import BytesIO

from PIL import Image
from boto3 import session
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from tqdm_loggable.auto import tqdm

logger = logging.getLogger("runpod upload utility")
FMT = "%(filename)-20s:%(lineno)-4d %(asctime)s %(message)s"
logging.basicConfig(level=logging.INFO, format=FMT, handlers=[logging.StreamHandler()])

# --------------------------- S3 Bucket Connection --------------------------- #
bucket_session = session.Session()

boto_config = Config(
    signature_version='s3v4',
    retries={
        'max_attempts': 3,
        'mode': 'standard'
    }
)

if os.environ.get('BUCKET_ENDPOINT_URL', None) is not None:
    boto_client = bucket_session.client(
        's3',
        endpoint_url=os.environ.get('BUCKET_ENDPOINT_URL', None),
        aws_access_key_id=os.environ.get('BUCKET_ACCESS_KEY_ID', None),
        aws_secret_access_key=os.environ.get('BUCKET_SECRET_ACCESS_KEY', None),
        config=boto_config
    )
else:
    boto_client = None  # pylint: disable=invalid-name


# ---------------------------------------------------------------------------- #
#                                 Upload Image                                 #
# ---------------------------------------------------------------------------- #
def upload_image(job_id, image_location, result_index=0, results_list=None):
    '''
    Upload image to bucket storage.
    '''
    image_name = str(uuid.uuid4())[:8]

    if boto_client is None:
        # Save the output to a file
        print("No bucket endpoint set, saving to disk folder 'simulated_uploaded'")
        print("If this is a live endpoint, please reference the following:")
        print("https://github.com/runpod/runpod-python/blob/main/docs/serverless/worker-utils.md")

        output = BytesIO()
        img = Image.open(image_location)
        img.save(output, format=img.format)

        os.makedirs("simulated_uploaded", exist_ok=True)
        with open(f"simulated_uploaded/{image_name}.png", "wb") as file_output:
            file_output.write(output.getvalue())

        if results_list is not None:
            results_list[result_index] = f"simulated_uploaded/{image_name}.png"

        return f"simulated_uploaded/{image_name}.png"

    output = BytesIO()
    img = Image.open(image_location)
    img.save(output, format=img.format)

    bucket = time.strftime('%m-%y')

    # Upload to S3
    boto_client.put_object(
        Bucket=f'{bucket}',
        Key=f'{job_id}/{image_name}.png',
        Body=output.getvalue(),
        ContentType="image/png"
    )

    output.close()

    presigned_url = boto_client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': f'{bucket}',
            'Key': f'{job_id}/{image_name}.png'
        }, ExpiresIn=604800)

    if results_list is not None:
        results_list[result_index] = presigned_url

    return presigned_url


# ---------------------------------------------------------------------------- #
#                                Files To Upload                               #
# ---------------------------------------------------------------------------- #
def files(job_id, file_list):
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
def bucket_upload(job_id, file_list, bucket_creds):
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
            f"{bucket_creds['endpointUrl']}/{bucket_creds['bucketName']}/{job_id}/{file}")

    return bucket_urls


# ------------------------- Single File Bucket Upload ------------------------ #
def file(file_name, file_location, bucket_creds):
    '''
    Uploads a single file to bucket storage.
    '''
    temp_bucket_session = session.Session()

    temp_boto_config = Config(
        signature_version='s3v4',
        retries={
            'max_attempts': 3,
            'mode': 'standard'
        }
    )

    temp_transfer_config = TransferConfig(
        multipart_threshold=1024 * 25,
        max_concurrency=10,
        multipart_chunksize=1024 * 25,
        use_threads=True
    )

    temp_boto_client = temp_bucket_session.client(
        's3',
        endpoint_url=bucket_creds['endpointUrl'],
        aws_access_key_id=bucket_creds['accessId'],
        aws_secret_access_key=bucket_creds['accessSecret'],
        config=temp_boto_config
    )

    file_size = os.path.getsize(file_location)
    with tqdm(total=file_size, unit='B', unit_scale=True, desc=file_name) as progress_bar:
        temp_boto_client.upload_file(
            file_location, str(bucket_creds['bucketName']), f'{file_name}',
            Config=temp_transfer_config,
            Callback=progress_bar.update
        )

    presigned_url = temp_boto_client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': f"{bucket_creds['bucketName']}",
            'Key': f"{file_name}"
        }, ExpiresIn=604800)

    return presigned_url
