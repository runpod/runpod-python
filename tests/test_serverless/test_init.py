"""Tests for runpod.serverless.__init__ module exports."""

import inspect
import runpod.serverless


class TestServerlessInit:
    """Test runpod.serverless module __all__ exports."""

    def test_all_defined(self):
        """Test that __all__ is defined in the module."""
        assert hasattr(runpod.serverless, '__all__')
        assert isinstance(runpod.serverless.__all__, list)
        assert len(runpod.serverless.__all__) > 0

    def test_all_symbols_importable(self):
        """Test that all symbols in __all__ are actually importable."""
        for symbol in runpod.serverless.__all__:
            assert hasattr(runpod.serverless, symbol), f"Symbol '{symbol}' in __all__ but not found in module"

    def test_expected_public_symbols(self):
        """Test that expected public symbols are in __all__."""
        expected_symbols = {
            'start',
            'progress_update', 
            'runpod_version'
        }
        actual_symbols = set(runpod.serverless.__all__)
        assert expected_symbols == actual_symbols, f"Expected {expected_symbols}, got {actual_symbols}"

    def test_start_function_accessible(self):
        """Test that start function is accessible and callable."""
        assert hasattr(runpod.serverless, 'start')
        assert callable(runpod.serverless.start)
        
        # Check function signature
        sig = inspect.signature(runpod.serverless.start)
        assert 'config' in sig.parameters

    def test_progress_update_accessible(self):
        """Test that progress_update is accessible and callable."""
        assert hasattr(runpod.serverless, 'progress_update')
        assert callable(runpod.serverless.progress_update)

    def test_runpod_version_accessible(self):
        """Test that runpod_version is accessible."""
        assert hasattr(runpod.serverless, 'runpod_version')
        assert isinstance(runpod.serverless.runpod_version, str)

    def test_private_symbols_not_exported(self):
        """Test that private symbols are not in __all__."""
        private_symbols = {
            '_set_config_args',
            '_get_realtime_port', 
            '_get_realtime_concurrency',
            '_signal_handler',
            'log',
            'parser'
        }
        all_symbols = set(runpod.serverless.__all__)
        
        for private_symbol in private_symbols:
            assert private_symbol not in all_symbols, f"Private symbol '{private_symbol}' should not be in __all__"

    def test_all_covers_public_api_only(self):
        """Test that __all__ contains only the intended public API."""
        # Get all non-private attributes from the module
        module_attrs = {name for name in dir(runpod.serverless) 
                       if not name.startswith('_')}
        
        # Filter out imported modules and types that shouldn't be public
        expected_private_attrs = {
            'argparse', 'json', 'os', 'signal', 'sys', 'time', 
            'worker', 'rp_fastapi', 'log', 'parser',
            'Any', 'Dict',  # Type hints
            'modules', 'utils',  # Sub-modules
            'RunPodLogger'  # Internal logger class
        }
        
        public_attrs = module_attrs - expected_private_attrs
        all_symbols = set(runpod.serverless.__all__)
        
        # All symbols in __all__ should be actual public API
        assert all_symbols.issubset(public_attrs), f"__all__ contains non-public symbols: {all_symbols - public_attrs}"
        
        # Expected public API should be exactly what's in __all__
        expected_public_api = {'start', 'progress_update', 'runpod_version'}
        assert all_symbols == expected_public_api, f"Expected {expected_public_api}, got {all_symbols}"
