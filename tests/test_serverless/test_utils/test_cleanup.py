'''
Test cleanup.py
'''

from unittest.mock import patch

from runpod.serverless.utils import rp_cleanup

def test_clean_no_folders():
    '''
    Test clean() with no folders.
    '''
    with patch('shutil.rmtree') as mock_rmtree, patch('os.remove') as mock_remove, \
        patch('os.path.exists', return_value=False):
        rp_cleanup.clean()
        assert mock_rmtree.call_count == 3
        mock_rmtree.assert_any_call("input_objects", ignore_errors=True)
        mock_rmtree.assert_any_call("output_objects", ignore_errors=True)
        mock_rmtree.assert_any_call("job_files", ignore_errors=True)
        mock_remove.assert_not_called()

def test_clean_with_output_zip():
    '''
    Test clean() with output.zip.
    '''
    with patch('shutil.rmtree') as mock_rmtree, patch('os.remove') as mock_remove, \
        patch('os.path.exists', return_value=True):
        rp_cleanup.clean()
        assert mock_rmtree.call_count == 3
        mock_remove.assert_called_once_with('output.zip')

def test_clean_with_folders():
    '''
    Test clean() with folders.
    '''
    with patch('shutil.rmtree') as mock_rmtree, patch('os.remove') as mock_remove, \
        patch('os.path.exists', return_value=False):
        rp_cleanup.clean(['test_folder1', 'test_folder2'])
        assert mock_rmtree.call_count == 5
        mock_rmtree.assert_any_call('test_folder1', ignore_errors=True)
        mock_rmtree.assert_any_call('test_folder2', ignore_errors=True)
        mock_remove.assert_not_called()
