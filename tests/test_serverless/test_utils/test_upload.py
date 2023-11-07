''' Tests for my_module | bucket utilities '''

import os
import io
import time
import importlib
import unittest
from unittest.mock import MagicMock, patch, Mock

from runpod.serverless.utils import rp_upload
from runpod.serverless.utils.rp_upload import get_boto_client
from runpod.serverless.utils import upload_file_to_bucket, upload_in_memory_object

BUCKET_CREDENTIALS = {
    'endpointUrl': 'https://your-bucket-endpoint-url.com',
    'accessId': 'your_access_key_id',
    'accessSecret': 'your_secret_access_key',
}


class TestBotoConfig(unittest.TestCase):
    ''' Tests for boto config '''

    def setUp(self) -> None:
        self.original_environ = os.environ.copy()
        self.mock_transfer_config = MagicMock()
        self.mock_boto_client = MagicMock()

    def tearDown(self):
        os.environ = self.original_environ

    def test_get_boto_client(self):
        '''
        Tests get_boto_client
        '''
        # Define the bucket credentials
        bucket_creds = BUCKET_CREDENTIALS

        # Mock boto3.session.Session
        with patch('boto3.session.Session') as mock_session, \
                patch('runpod.serverless.utils.rp_upload.TransferConfig') as mock_transfer_config:
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
                's3',
                endpoint_url=bucket_creds['endpointUrl'],
                aws_access_key_id=bucket_creds['accessId'],
                aws_secret_access_key=bucket_creds['accessSecret'],
                config=unittest.mock.ANY,
                region_name=None
            )

            creds_s3 = bucket_creds.copy()
            creds_s3['endpointUrl'] = "https://bucket-name.s3.region-code.amazonaws.com/key-name"

            boto_client, transfer_config = get_boto_client(creds_s3)

            mock_session.return_value.client.assert_called_with(
                's3',
                endpoint_url=creds_s3['endpointUrl'],
                aws_access_key_id=bucket_creds['accessId'],
                aws_secret_access_key=bucket_creds['accessSecret'],
                config=unittest.mock.ANY,
                region_name="region-code"
            )

            creds_do = bucket_creds.copy()
            creds_do['endpointUrl'] = "https://name.region-code.digitaloceanspaces.com/key-name"

            boto_client, transfer_config = get_boto_client(creds_do)

            mock_session.return_value.client.assert_called_with(
                's3',
                endpoint_url=creds_do['endpointUrl'],
                aws_access_key_id=bucket_creds['accessId'],
                aws_secret_access_key=bucket_creds['accessSecret'],
                config=unittest.mock.ANY,
                region_name="region-code"
            )

    def test_get_boto_client_environ(self):
        '''
        Tests get_boto_client with environment variables
        '''
        assert rp_upload.get_boto_client()[0] is None

        os.environ['BUCKET_ENDPOINT_URL'] = 'https://your-bucket-endpoint-url.com'
        os.environ['BUCKET_ACCESS_KEY_ID'] = 'your_access_key_id'
        os.environ['BUCKET_SECRET_ACCESS_KEY'] = 'your_secret_access_key'

        importlib.reload(rp_upload)

        with patch('boto3.session.Session') as mock_session, \
                patch('runpod.serverless.utils.rp_upload.TransferConfig') as mock_transfer_config:
            mock_session.return_value.client.return_value = self.mock_boto_client
            mock_transfer_config.return_value = self.mock_transfer_config

            boto_client, transfer_config = rp_upload.get_boto_client()

            assert boto_client == self.mock_boto_client
            assert transfer_config == self.mock_transfer_config

# ---------------------------------------------------------------------------- #
#                                 Upload Image                                 #
# ---------------------------------------------------------------------------- #


class TestUploadImage(unittest.TestCase):
    ''' Tests for upload_image '''

    @patch("runpod.serverless.utils.rp_upload.get_boto_client")
    @patch("builtins.open")
    @patch("runpod.serverless.utils.rp_upload.os.makedirs")
    def test_upload_image_local(self, mock_makedirs, mock_open, mock_get_boto_client):
        '''
        Test upload_image function when there is no boto client
        '''
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
        '''
        Test upload_image function when there is a boto client
        '''
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


class TestUploadUtility(unittest.TestCase):
    ''' Tests for upload utility '''

    @patch('runpod.serverless.utils.rp_upload.get_boto_client')
    def test_upload_file_to_bucket(self, mock_get_boto_client):
        '''
        Tests upload_file_to_bucket
        '''
        # Mock boto_client and transfer_config
        mock_boto_client = MagicMock()
        mock_transfer_config = MagicMock()

        mock_get_boto_client.return_value = (mock_boto_client, mock_transfer_config)

        # Define the file name and file location
        file_name = 'example.txt'
        file_location = '/path/to/your/local/file/example.txt'

        # Mock os.path.getsize to return a file size
        with patch('os.path.getsize', return_value=1024):
            upload_file_to_bucket(file_name, file_location, BUCKET_CREDENTIALS)

        # Check if get_boto_client was called with the correct arguments
        mock_get_boto_client.assert_called_once_with(BUCKET_CREDENTIALS)

        # Check if upload_file was called with the correct arguments
        upload_file_args = {
            'Filename': file_location,
            'Bucket': str(time.strftime('%m-%y')),
            'Key': file_name,
            'Config': mock_transfer_config,
            'Callback': unittest.mock.ANY
        }
        mock_boto_client.upload_file.assert_called_once_with(**upload_file_args)

        # Check if generate_presigned_url was called with the correct arguments
        mock_boto_client.generate_presigned_url.assert_called_once_with(
            'get_object',
            Params={
                'Bucket': str(time.strftime('%m-%y')),
                'Key': file_name
            }, ExpiresIn=604800
        )

    @patch('runpod.serverless.utils.rp_upload.get_boto_client')
    def test_upload_in_memory_object(self, mock_get_boto_client):
        '''
        Tests upload_in_memory_object
        '''
        # Mock boto_client and transfer_config
        mock_boto_client = MagicMock()
        mock_transfer_config = MagicMock()

        mock_get_boto_client.return_value = (mock_boto_client, mock_transfer_config)

        # Define the file name and file data (bytes)
        file_name = 'example.txt'
        file_data = b'This is an example text.'

        upload_in_memory_object(file_name, file_data, BUCKET_CREDENTIALS)

        # Check if get_boto_client was called with the correct arguments
        mock_get_boto_client.assert_called_once_with(BUCKET_CREDENTIALS)

        # Check if upload_fileobj was called with the correct arguments
        mock_boto_client.upload_fileobj.assert_called_once_with(
            unittest.mock.ANY,
            str(time.strftime('%m-%y')),
            file_name,
            Config=mock_transfer_config,
            Callback=unittest.mock.ANY
        )

        # Check if BytesIO was called with the correct arguments
        file_data_buffer = mock_boto_client.upload_fileobj.call_args[0][0]
        self.assertIsInstance(file_data_buffer, io.BytesIO)

        # Check if generate_presigned_url was called with the correct arguments
        mock_boto_client.generate_presigned_url.assert_called_once_with(
            'get_object',
            Params={
                'Bucket': str(time.strftime('%m-%y')),
                'Key': file_name
            }, ExpiresIn=604800
        )
