"""Tests for runpod.endpoint.__init__ module exports."""

import inspect
import runpod.endpoint


class TestEndpointInit:
    """Test runpod.endpoint module __all__ exports."""

    def test_all_defined(self):
        """Test that __all__ is defined in the module."""
        assert hasattr(runpod.endpoint, '__all__')
        assert isinstance(runpod.endpoint.__all__, list)
        assert len(runpod.endpoint.__all__) > 0

    def test_all_symbols_importable(self):
        """Test that all symbols in __all__ are actually importable."""
        for symbol in runpod.endpoint.__all__:
            assert hasattr(runpod.endpoint, symbol), f"Symbol '{symbol}' in __all__ but not found in module"

    def test_expected_public_symbols(self):
        """Test that expected public symbols are in __all__."""
        expected_symbols = {
            'AsyncioEndpoint',
            'AsyncioJob',
            'Endpoint', 
            'Job'
        }
        actual_symbols = set(runpod.endpoint.__all__)
        assert expected_symbols == actual_symbols, f"Expected {expected_symbols}, got {actual_symbols}"

    def test_endpoint_classes_accessible(self):
        """Test that endpoint classes are accessible and are classes."""
        endpoint_classes = ['AsyncioEndpoint', 'AsyncioJob', 'Endpoint', 'Job']
        
        for class_name in endpoint_classes:
            assert class_name in runpod.endpoint.__all__
            assert hasattr(runpod.endpoint, class_name)
            assert inspect.isclass(getattr(runpod.endpoint, class_name))

    def test_asyncio_classes_distinct(self):
        """Test that asyncio classes are distinct from sync classes."""
        assert runpod.endpoint.AsyncioEndpoint != runpod.endpoint.Endpoint
        assert runpod.endpoint.AsyncioJob != runpod.endpoint.Job

    def test_no_duplicate_symbols_in_all(self):
        """Test that __all__ contains no duplicate symbols."""
        all_symbols = runpod.endpoint.__all__
        unique_symbols = set(all_symbols)
        assert len(all_symbols) == len(unique_symbols), f"Duplicates found in __all__: {[x for x in all_symbols if all_symbols.count(x) > 1]}"

    def test_all_covers_public_api_only(self):
        """Test that __all__ contains only the intended public API."""
        # Get all non-private attributes from the module
        module_attrs = {name for name in dir(runpod.endpoint) 
                       if not name.startswith('_')}
        
        # Filter out imported modules that shouldn't be public
        expected_private_attrs = set()  # No private imports in this module
        
        public_attrs = module_attrs - expected_private_attrs
        all_symbols = set(runpod.endpoint.__all__)
        
        # All symbols in __all__ should be actual public API
        assert all_symbols.issubset(public_attrs), f"__all__ contains non-public symbols: {all_symbols - public_attrs}"
        
        # Expected public API should be exactly what's in __all__
        expected_public_api = {'AsyncioEndpoint', 'AsyncioJob', 'Endpoint', 'Job'}
        assert all_symbols == expected_public_api, f"Expected {expected_public_api}, got {all_symbols}"