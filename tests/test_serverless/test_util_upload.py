''' Tests for my_module | bucket utilities '''

import io
import time
import unittest
from unittest.mock import MagicMock, patch

from runpod.serverless.utils import upload_file_to_bucket, upload_in_memory_object

BUCKET_CREDENTIALS = {
    'endpointUrl': 'https://your-bucket-endpoint-url.com',
    'accessId': 'your_access_key_id',
    'accessSecret': 'your_secret_access_key',
}


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
        mock_boto_client.upload_file.assert_called_once_with(
            file_location, str(time.strftime('%m-%y')), file_name,
            Config=mock_transfer_config,
            Callback=unittest.mock.ANY
        )

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
            unittest.mock.ANY, str(time.strftime('%m-%y')), file_name,
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
