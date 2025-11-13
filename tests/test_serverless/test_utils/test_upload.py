""" Tests for my_module | bucket utilities """

import importlib
import io
import os
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from runpod.serverless.utils import (
    rp_upload,
    upload_file_to_bucket,
    upload_in_memory_object,
)
from runpod.serverless.utils.rp_upload import get_boto_client

BUCKET_CREDENTIALS = {
    "endpointUrl": "https://your-bucket-endpoint-url.com",
    "accessId": "your_access_key_id",
    "accessSecret": "your_secret_access_key",
}


class TestBotoConfig(unittest.TestCase):
    """Tests for boto config"""

    def setUp(self) -> None:
        self.original_environ = os.environ.copy()
        self.mock_transfer_config = MagicMock()
        self.mock_boto_client = MagicMock()

    def tearDown(self):
        os.environ = self.original_environ

    def test_import_boto3_dependencies_missing(self):
        """
        Tests _import_boto3_dependencies when boto3 is not available
        """
        with patch("builtins.__import__", side_effect=ImportError("No module named 'boto3'")):
            with self.assertRaises(ImportError) as context:
                rp_upload._import_boto3_dependencies()
            self.assertIn("boto3 is required for S3 upload functionality", str(context.exception))

    def test_get_boto_client(self):
        """
        Tests get_boto_client
        """
        # Define the bucket credentials
        bucket_creds = BUCKET_CREDENTIALS

        # Mock boto3 imports (now lazy-loaded inside the function)
        with patch("boto3.session.Session") as mock_session, patch(
            "boto3.s3.transfer.TransferConfig"
        ) as mock_transfer_config:
            mock_session.return_value.client.return_value = self.mock_boto_client
            mock_transfer_config.return_value = self.mock_transfer_config

            # Call get_boto_client
            boto_client, transfer_config = get_boto_client(bucket_creds)

            # Check if boto_client and transfer_config are correct
            self.assertEqual(boto_client, self.mock_boto_client)
            self.assertEqual(transfer_config, self.mock_transfer_config)

            # Check if boto3.session.Session was called with the correct arguments
            mock_session.assert_called_once_with()

            # Check if boto_client was called with the correct arguments
            mock_session.return_value.client.assert_called_once_with(
                "s3",
                endpoint_url=bucket_creds["endpointUrl"],
                aws_access_key_id=bucket_creds["accessId"],
                aws_secret_access_key=bucket_creds["accessSecret"],
                config=unittest.mock.ANY,
                region_name=None,
            )

            creds_s3 = bucket_creds.copy()
            creds_s3["endpointUrl"] = (
                "https://bucket-name.s3.region-code.amazonaws.com/key-name"
            )

            boto_client, transfer_config = get_boto_client(creds_s3)

            mock_session.return_value.client.assert_called_with(
                "s3",
                endpoint_url=creds_s3["endpointUrl"],
                aws_access_key_id=bucket_creds["accessId"],
                aws_secret_access_key=bucket_creds["accessSecret"],
                config=unittest.mock.ANY,
                region_name="region-code",
            )

            creds_do = bucket_creds.copy()
            creds_do["endpointUrl"] = (
                "https://name.region-code.digitaloceanspaces.com/key-name"
            )

            boto_client, transfer_config = get_boto_client(creds_do)

            mock_session.return_value.client.assert_called_with(
                "s3",
                endpoint_url=creds_do["endpointUrl"],
                aws_access_key_id=bucket_creds["accessId"],
                aws_secret_access_key=bucket_creds["accessSecret"],
                config=unittest.mock.ANY,
                region_name="region-code",
            )

    def test_get_boto_client_environ(self):
        """
        Tests get_boto_client with environment variables
        """
        assert rp_upload.get_boto_client()[0] is None

        os.environ["BUCKET_ENDPOINT_URL"] = "https://your-bucket-endpoint-url.com"
        os.environ["BUCKET_ACCESS_KEY_ID"] = "your_access_key_id"
        os.environ["BUCKET_SECRET_ACCESS_KEY"] = "your_secret_access_key"

        importlib.reload(rp_upload)

        # Mock boto3 imports (now lazy-loaded inside the function)
        with patch("boto3.session.Session") as mock_session, patch(
            "boto3.s3.transfer.TransferConfig"
        ) as mock_transfer_config:
            mock_session.return_value.client.return_value = self.mock_boto_client
            mock_transfer_config.return_value = self.mock_transfer_config

            boto_client, transfer_config = rp_upload.get_boto_client()

            assert boto_client == self.mock_boto_client
            assert transfer_config == self.mock_transfer_config


# ---------------------------------------------------------------------------- #
#                                 Upload Image                                 #
# ---------------------------------------------------------------------------- #


class TestUploadImage(unittest.TestCase):
    """Tests for upload_image"""

    @patch("runpod.serverless.utils.rp_upload.get_boto_client")
    @patch("builtins.open")
    @patch("runpod.serverless.utils.rp_upload.os.makedirs")
    def test_upload_image_local(self, mock_makedirs, mock_open, mock_get_boto_client):
        """
        Test upload_image function when there is no boto client
        """
        # Mocking get_boto_client to return None
        mock_get_boto_client.return_value = (None, None)

        # Mocking the context manager of Image.open
        mock_file = mock_open.return_value.__enter__.return_value
        mock_file.read.return_value = b"simulated_uploaded"
        mock_file.__exit__.return_value = False

        result = rp_upload.upload_image("job_id", "image_location")

        # Assert that image is saved locally
        assert "simulated_uploaded" in result
        mock_makedirs.assert_called_once()

    @patch("runpod.serverless.utils.rp_upload.get_boto_client")
    @patch("builtins.open")
    def test_upload_image_s3(self, mock_open, mock_get_boto_client):
        """
        Test upload_image function when there is a boto client
        """
        # Mocking boto_client
        mock_boto_client = Mock()
        mock_boto_client.put_object = Mock()
        mock_boto_client.generate_presigned_url = Mock(return_value="presigned_url")

        mock_get_boto_client.return_value = (mock_boto_client, None)

        # Mocking the context manager of Image.open
        mock_image = Mock()
        mock_image.format = "PNG"
        mock_open.return_value.__enter__.return_value = mock_image

        result = rp_upload.upload_image("job_id", "image_location")

        # Assert the image is uploaded to S3
        assert result == "presigned_url"
        mock_open.assert_called_once_with("image_location", "rb")
        mock_boto_client.put_object.assert_called_once()
        mock_boto_client.generate_presigned_url.assert_called_once()


class TestLocalFallback(unittest.TestCase):
    """Tests for _save_to_local_fallback helper function"""

    @patch("os.makedirs")
    def test_save_to_local_fallback_invalid_args(self, mock_makedirs):
        """
        Tests _save_to_local_fallback raises ValueError when neither source_path nor file_data provided
        """
        with self.assertRaises(ValueError) as context:
            rp_upload._save_to_local_fallback("test.txt")
        self.assertIn("Either source_path or file_data must be provided", str(context.exception))


class TestUploadUtility(unittest.TestCase):
    """Tests for upload utility"""

    @patch("runpod.serverless.utils.rp_upload.get_boto_client")
    @patch("os.path.exists")
    @patch("shutil.copyfile")
    @patch("os.makedirs")
    def test_upload_file_to_bucket_fallback(
        self, mock_makedirs, mock_copyfile, mock_exists, mock_get_boto_client
    ):
        """
        Tests upload_file_to_bucket fallback when boto_client is None
        """
        # Mock get_boto_client to return None
        mock_get_boto_client.return_value = (None, None)
        mock_exists.return_value = True

        file_name = "example.txt"
        file_location = "/path/to/file.txt"

        result = upload_file_to_bucket(file_name, file_location)

        # Check fallback behavior
        assert result == "local_upload/example.txt"
        mock_makedirs.assert_called_once_with("local_upload", exist_ok=True)
        mock_copyfile.assert_called_once_with(file_location, "local_upload/example.txt")

    @patch("runpod.serverless.utils.rp_upload.get_boto_client")
    def test_upload_file_to_bucket(self, mock_get_boto_client):
        """
        Tests upload_file_to_bucket
        """
        # Mock boto_client and transfer_config
        mock_boto_client = MagicMock()
        mock_transfer_config = MagicMock()

        mock_get_boto_client.return_value = (mock_boto_client, mock_transfer_config)

        # Define the file name and file location
        file_name = "example.txt"
        file_location = "/path/to/your/local/file/example.txt"

        # Mock os.path.getsize to return a file size
        with patch("os.path.getsize", return_value=1024):
            upload_file_to_bucket(file_name, file_location, BUCKET_CREDENTIALS)

        # Check if get_boto_client was called with the correct arguments
        mock_get_boto_client.assert_called_once_with(BUCKET_CREDENTIALS)

        # Check if upload_file was called with the correct arguments
        upload_file_args = {
            "Filename": file_location,
            "Bucket": str(time.strftime("%m-%y")),
            "Key": file_name,
            "Config": mock_transfer_config,
            "Callback": unittest.mock.ANY,
        }
        mock_boto_client.upload_file.assert_called_once_with(**upload_file_args)

        # Check if generate_presigned_url was called with the correct arguments
        mock_boto_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": str(time.strftime("%m-%y")), "Key": file_name},
            ExpiresIn=604800,
        )

    @patch("runpod.serverless.utils.rp_upload.get_boto_client")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_upload_in_memory_object_fallback(
        self, mock_makedirs, mock_open_file, mock_get_boto_client
    ):
        """
        Tests upload_in_memory_object fallback when boto_client is None
        """
        # Mock get_boto_client to return None
        mock_get_boto_client.return_value = (None, None)

        file_name = "example.txt"
        file_data = b"This is test data."

        result = upload_in_memory_object(file_name, file_data)

        # Check fallback behavior
        assert result == "local_upload/example.txt"
        mock_makedirs.assert_called_once_with("local_upload", exist_ok=True)
        mock_open_file.assert_called_once_with("local_upload/example.txt", "wb")
        mock_open_file().write.assert_called_once_with(file_data)

    @patch("runpod.serverless.utils.rp_upload.get_boto_client")
    def test_upload_in_memory_object(self, mock_get_boto_client):
        """
        Tests upload_in_memory_object
        """
        # Mock boto_client and transfer_config
        mock_boto_client = MagicMock()
        mock_transfer_config = MagicMock()

        mock_get_boto_client.return_value = (mock_boto_client, mock_transfer_config)

        # Define the file name and file data (bytes)
        file_name = "example.txt"
        file_data = b"This is an example text."

        upload_in_memory_object(file_name, file_data, BUCKET_CREDENTIALS)

        # Check if get_boto_client was called with the correct arguments
        mock_get_boto_client.assert_called_once_with(BUCKET_CREDENTIALS)

        # Check if upload_fileobj was called with the correct arguments
        mock_boto_client.upload_fileobj.assert_called_once_with(
            unittest.mock.ANY,
            str(time.strftime("%m-%y")),
            file_name,
            Config=mock_transfer_config,
            Callback=unittest.mock.ANY,
        )

        # Check if BytesIO was called with the correct arguments
        file_data_buffer = mock_boto_client.upload_fileobj.call_args[0][0]
        self.assertIsInstance(file_data_buffer, io.BytesIO)

        # Check if generate_presigned_url was called with the correct arguments
        mock_boto_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": str(time.strftime("%m-%y")), "Key": file_name},
            ExpiresIn=604800,
        )
