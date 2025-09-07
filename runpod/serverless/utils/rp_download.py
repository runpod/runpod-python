"""
PodWorker | modules | download.py

Called when inputs are images or zip files.
Downloads them into a temporary directory called "input_objects".
This directory is cleaned up after the job is complete.
"""

import os
import re
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import List, Union, Dict
from urllib.parse import urlparse

import backoff
from requests import RequestException

from runpod.http_client import SyncClientSession

HEADERS = {"User-Agent": "runpod-python/0.0.0 (https://runpod.io; support@runpod.io)"}


def calculate_chunk_size(file_size: int) -> int:
    """
    Calculates the chunk size based on the file size.
    """
    if file_size <= 1024 * 1024:  # 1 MB
        return 1024  # 1 KB
    if file_size <= 1024 * 1024 * 1024:  # 1 GB
        return 1024 * 1024  # 1 MB

    return 1024 * 1024 * 10  # 10 MB


def extract_disposition_params(content_disposition: str) -> Dict[str, str]:
    parts = (p.strip() for p in content_disposition.split(";"))

    params = {
        key.strip().lower(): value.strip().strip('"')
        for part in parts
        if "=" in part
        for key, value in [part.split("=", 1)]
    }

    return params


def download_files_from_urls(job_id: str, urls: Union[str, List[str]]) -> List[str]:
    """
    Accepts a single URL or a list of URLs and downloads the files.
    Returns the list of downloaded file absolute paths.
    Saves the files in a directory called "downloaded_files" in the job directory.
    """
    download_directory = os.path.abspath(os.path.join("jobs", job_id, "downloaded_files"))
    os.makedirs(download_directory, exist_ok=True)

    @backoff.on_exception(backoff.expo, RequestException, max_tries=3)
    def download_file(url: str, path_to_save: str) -> str:
        with SyncClientSession().get(url, headers=HEADERS, stream=True, timeout=5) as response:
            response.raise_for_status()
            content_disposition = response.headers.get("Content-Disposition")
            file_extension = ""
            if content_disposition:
                params = extract_disposition_params(content_disposition)
                file_extension = os.path.splitext(params.get("filename", ""))[1]

            # If no extension could be determined from 'Content-Disposition', get it from the URL
            if not file_extension:
                file_extension = os.path.splitext(urlparse(url).path)[1]

            file_size = int(response.headers.get("Content-Length", 0))
            chunk_size = calculate_chunk_size(file_size)

            # write the content in chunks to the file
            with open(path_to_save + file_extension, "wb") as file_path:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:  # filter out keep-alive chunks
                        file_path.write(chunk)

            return file_extension

    def download_file_to_path(url: str) -> str:
        if url is None:
            return None

        file_name = f"{uuid.uuid4()}"
        output_file_path = os.path.join(download_directory, file_name)

        try:
            file_extension = download_file(url, output_file_path)
        except RequestException as err:
            print(f"Failed to download {url}: {err}")
            return None

        return os.path.abspath(f"{output_file_path}{file_extension}")

    if isinstance(urls, str):
        urls = [urls]

    with ThreadPoolExecutor() as executor:
        downloaded_files = list(executor.map(download_file_to_path, urls))

    return downloaded_files


def file(file_url: str) -> dict:
    """
    Downloads a single file from a given URL, file is given a random name.
    First checks if the content-disposition header is set, if so, uses the file name from there.
    If the file is a zip file, it is extracted into a directory with the same name.

    Returns an object that contains:
    - The absolute path to the downloaded file
    - File type
    - Original file name
    """
    os.makedirs("job_files", exist_ok=True)

    download_response = SyncClientSession().get(file_url, headers=HEADERS, timeout=30)

    content_disposition = download_response.headers.get("Content-Disposition")

    original_file_name = ""
    if content_disposition:
        params = extract_disposition_params(content_disposition)

        original_file_name = params.get("filename", "")

    if not original_file_name:
        download_path = urlparse(file_url).path
        original_file_name = os.path.basename(download_path)

    file_type = os.path.splitext(original_file_name)[1].replace(".", "")

    file_name = f"{uuid.uuid4()}"

    output_file_path = os.path.join("job_files", f"{file_name}.{file_type}")
    with open(output_file_path, "wb") as output_file:
        output_file.write(download_response.content)

    if file_type == "zip":
        unzipped_directory = os.path.join("job_files", file_name)
        os.makedirs(unzipped_directory, exist_ok=True)
        with zipfile.ZipFile(output_file_path, "r") as zip_ref:
            zip_ref.extractall(unzipped_directory)
        unzipped_directory = os.path.abspath(unzipped_directory)
    else:
        unzipped_directory = None

    return {
        "file_path": os.path.abspath(output_file_path),
        "type": file_type,
        "original_name": original_file_name,
        "extracted_path": unzipped_directory,
    }
