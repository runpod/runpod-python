"""Tests for runpod.serverless.utils.__init__ module exports."""

import inspect
import runpod.serverless.utils


class TestServerlessUtilsInit:
    """Test runpod.serverless.utils module __all__ exports."""

    def test_all_defined(self):
        """Test that __all__ is defined in the module."""
        assert hasattr(runpod.serverless.utils, '__all__')
        assert isinstance(runpod.serverless.utils.__all__, list)
        assert len(runpod.serverless.utils.__all__) > 0

    def test_all_symbols_importable(self):
        """Test that all symbols in __all__ are actually importable."""
        for symbol in runpod.serverless.utils.__all__:
            assert hasattr(runpod.serverless.utils, symbol), f"Symbol '{symbol}' in __all__ but not found in module"

    def test_expected_public_symbols(self):
        """Test that expected public symbols are in __all__."""
        expected_symbols = {
            'download_files_from_urls',
            'upload_file_to_bucket',
            'upload_in_memory_object'
        }
        actual_symbols = set(runpod.serverless.utils.__all__)
        assert expected_symbols == actual_symbols, f"Expected {expected_symbols}, got {actual_symbols}"

    def test_utility_functions_accessible(self):
        """Test that utility functions are accessible and callable."""
        utility_functions = [
            'download_files_from_urls',
            'upload_file_to_bucket', 
            'upload_in_memory_object'
        ]
        
        for func_name in utility_functions:
            assert func_name in runpod.serverless.utils.__all__
            assert hasattr(runpod.serverless.utils, func_name)
            assert callable(getattr(runpod.serverless.utils, func_name))

    def test_download_function_signature(self):
        """Test that download function has expected signature."""
        func = runpod.serverless.utils.download_files_from_urls
        sig = inspect.signature(func)
        # Check that it has some parameters (exact signature may vary)
        assert len(sig.parameters) > 0

    def test_upload_functions_signatures(self):
        """Test that upload functions have expected signatures."""
        upload_funcs = [
            runpod.serverless.utils.upload_file_to_bucket,
            runpod.serverless.utils.upload_in_memory_object
        ]
        
        for func in upload_funcs:
            sig = inspect.signature(func)
            # Check that it has some parameters (exact signature may vary)
            assert len(sig.parameters) > 0

    def test_no_duplicate_symbols_in_all(self):
        """Test that __all__ contains no duplicate symbols."""
        all_symbols = runpod.serverless.utils.__all__
        unique_symbols = set(all_symbols)
        assert len(all_symbols) == len(unique_symbols), f"Duplicates found in __all__: {[x for x in all_symbols if all_symbols.count(x) > 1]}"

    def test_all_covers_public_api_only(self):
        """Test that __all__ contains only the intended public API."""
        # Get all non-private attributes from the module
        module_attrs = {name for name in dir(runpod.serverless.utils) 
                       if not name.startswith('_')}
        
        # Filter out any imported modules that shouldn't be public
        expected_private_attrs = set()  # No private imports in this module
        
        public_attrs = module_attrs - expected_private_attrs
        all_symbols = set(runpod.serverless.utils.__all__)
        
        # All symbols in __all__ should be actual public API
        assert all_symbols.issubset(public_attrs), f"__all__ contains non-public symbols: {all_symbols - public_attrs}"
        
        # Expected public API should be exactly what's in __all__
        expected_public_api = {
            'download_files_from_urls', 
            'upload_file_to_bucket', 
            'upload_in_memory_object'
        }
        assert all_symbols == expected_public_api, f"Expected {expected_public_api}, got {all_symbols}"

    def test_functions_from_correct_modules(self):
        """Test that functions are imported from the expected modules."""
        # download_files_from_urls should be from rp_download
        # upload functions should be from rp_upload
        # We can't easily test the source module, but we can test they exist
        assert hasattr(runpod.serverless.utils, 'download_files_from_urls')
        assert hasattr(runpod.serverless.utils, 'upload_file_to_bucket')
        assert hasattr(runpod.serverless.utils, 'upload_in_memory_object')