"""Tests for runpod | serverless | utils | rp_download.py"""

# pylint: disable=R0903,W0613

import os
import unittest
from unittest.mock import mock_open, patch

from requests import RequestException

from runpod.serverless.utils.rp_download import (
    calculate_chunk_size,
    download_files_from_urls,
    file,
)

URL_LIST = [
    "https://example.com/picture.jpg",
    "https://example.com/picture.jpg?X-Amz-Signature=123",
    "https://example.com/file_without_extension",
]

JOB_ID = "job_123"


class MockResponse:
    """Stand-in for the streamed requests.Response returned by safe_get."""

    def __init__(self, content, status_code, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise RequestException(f"Status code: {self.status_code}")

    def iter_content(self, chunk_size=1024):
        """A fresh generator each call (safe_get responses are streamed once)."""
        content = self.content or b""
        for i in range(0, len(content), chunk_size):
            yield content[i : min(i + chunk_size, len(content))]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def mock_safe_get(*args, **kwargs):
    """Mocks rp_ssrf.safe_get for the download_files_from_urls tests."""
    headers = {
        "Content-Disposition": 'attachment; filename="picture.jpg"',
        "Content-Length": "1000",
    }
    url = args[0]
    if any(url.startswith(base_url) for base_url in URL_LIST):
        return MockResponse(b"nothing", 200, headers)
    return MockResponse(None, 404)


def _called_urls(mock):
    """URLs passed positionally to safe_get, order-independent."""
    return {call.args[0] for call in mock.call_args_list}


class TestDownloadFilesFromUrls(unittest.TestCase):
    """Tests for download_files_from_urls"""

    def test_calculate_chunk_size(self):
        """
        Tests calculate_chunk_size
        """
        self.assertEqual(calculate_chunk_size(1024), 1024)
        self.assertEqual(calculate_chunk_size(1024 * 1024), 1024)
        self.assertEqual(calculate_chunk_size(1024 * 1024 * 1024), 1024 * 1024)
        self.assertEqual(calculate_chunk_size(1024 * 1024 * 1024 * 10), 1024 * 1024 * 10)

    @patch("os.makedirs", return_value=None)
    @patch("runpod.serverless.utils.rp_download.safe_get", side_effect=mock_safe_get)
    @patch("builtins.open", new_callable=mock_open)
    def test_download_files_from_urls(self, mock_open_file, mock_get, mock_makedirs):
        """
        Tests download_files_from_urls
        """
        urls = ("https://example.com/picture.jpg", "https://example.com/file_without_extension",)
        downloaded_files = download_files_from_urls(
            JOB_ID,
            urls,
        )

        self.assertEqual(len(downloaded_files), len(urls))

        # download runs concurrently, so assert the set of fetched URLs (not order)
        self.assertEqual(_called_urls(mock_get), set(urls))

        for index in range(len(urls)):
            # Check that the file has the correct extension
            self.assertTrue(downloaded_files[index].endswith(".jpg"))
            mock_open_file.assert_any_call(downloaded_files[index], "wb")

        mock_makedirs.assert_called_once_with(os.path.abspath(f"jobs/{JOB_ID}/downloaded_files"), exist_ok=True)

        string_download_file = download_files_from_urls(JOB_ID, "https://example.com/picture.jpg")
        self.assertTrue(string_download_file[0].endswith(".jpg"))

        # Check if None is returned when url is None
        self.assertEqual(download_files_from_urls(JOB_ID, [None]), [None])

        # Test requests exception
        mock_get.side_effect = RequestException("Error")
        self.assertEqual(
            download_files_from_urls(JOB_ID, ["https://example.com/picture.jpg"]),
            [None],
        )

    @patch("os.makedirs", return_value=None)
    @patch("runpod.serverless.utils.rp_download.safe_get", side_effect=mock_safe_get)
    @patch("builtins.open", new_callable=mock_open)
    def test_download_files_from_urls_signed(self, mock_open_file, mock_get, mock_makedirs):
        """
        Tests download_files_from_urls with signed urls
        """
        downloaded_files = download_files_from_urls(
            JOB_ID,
            [
                "https://example.com/picture.jpg?X-Amz-Signature=123",
            ],
        )

        # Confirms that the same number of files were downloaded as urls provided
        self.assertEqual(len(downloaded_files), 1)

        # Check that the signed url was fetched
        self.assertEqual(_called_urls(mock_get), {URL_LIST[1]})

        # Check that the file has the correct extension
        self.assertTrue(downloaded_files[0].endswith(".jpg"))

        mock_open_file.assert_called_once_with(downloaded_files[0], "wb")
        mock_makedirs.assert_called_once_with(os.path.abspath(f"jobs/{JOB_ID}/downloaded_files"), exist_ok=True)


class FileDownloaderTestCase(unittest.TestCase):
    """Tests for file()"""

    @patch("runpod.serverless.utils.rp_download.safe_get")
    @patch("builtins.open", new_callable=mock_open)
    def test_download_file(self, mock_file, mock_get):
        """
        Tests file()
        """
        mock_get.return_value = MockResponse(
            b"file content", 200, {"Content-Disposition": "filename=test_file.txt"}
        )

        result = file("http://test.com/test_file.txt")

        self.assertEqual(result["type"], "txt")
        self.assertEqual(result["original_name"], "test_file.txt")
        self.assertTrue(result["file_path"].endswith(".txt"))
        self.assertIsNone(result["extracted_path"])

        # Body is streamed to disk in a single chunk here
        mock_file().write.assert_called_once_with(b"file content")

    @patch("runpod.serverless.utils.rp_download.safe_get")
    @patch("builtins.open", new_callable=mock_open)
    def test_download_file_with_content_disposition(self, mock_file, mock_get):
        """
        Tests file() using filename from Content-Disposition
        """
        mock_get.return_value = MockResponse(
            b"file content", 200, {"Content-Disposition": 'inline; filename="test_file.txt"'}
        )

        result = file("http://test.com/file_without_extension")

        self.assertEqual(result["type"], "txt")
        self.assertEqual(result["original_name"], "test_file.txt")
        self.assertTrue(result["file_path"].endswith(".txt"))
        self.assertIsNone(result["extracted_path"])

        mock_file().write.assert_called_once_with(b"file content")

    @patch("runpod.serverless.utils.rp_download.safe_get")
    @patch("builtins.open", new_callable=mock_open)
    @patch("runpod.serverless.utils.rp_download.zipfile.ZipFile")
    def test_download_zip_file(self, mock_zip, mock_file, mock_get):
        """
        Tests file() with a zip file
        """
        mock_get.return_value = MockResponse(
            b"zip file content", 200, {"Content-Disposition": "filename=test_file.zip"}
        )

        result = file("http://test.com/test_file.zip")

        self.assertEqual(result["type"], "zip")
        self.assertEqual(result["original_name"], "test_file.zip")
        self.assertTrue(result["file_path"].endswith(".zip"))
        self.assertIsNotNone(result["extracted_path"])

        mock_file().write.assert_called_once_with(b"zip file content")

        # Check if no file name is provided (falls back to URL basename)
        mock_get.return_value = MockResponse(b"zip file content", 200, {"Content-Disposition": ""})
        result = file("http://test.com/test_file.zip")
        self.assertEqual(result["original_name"], "test_file.zip")


if __name__ == "__main__":
    unittest.main()
