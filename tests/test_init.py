"""Tests for runpod.__init__ module exports."""

import inspect
import runpod


class TestRunpodInit:
    """Test runpod module __all__ exports."""

    def test_all_defined(self):
        """Test that __all__ is defined in the module."""
        assert hasattr(runpod, '__all__')
        assert isinstance(runpod.__all__, list)
        assert len(runpod.__all__) > 0

    def test_all_symbols_importable(self):
        """Test that all symbols in __all__ are actually importable."""
        for symbol in runpod.__all__:
            assert hasattr(runpod, symbol), f"Symbol '{symbol}' in __all__ but not found in module"

    def test_api_functions_accessible(self):
        """Test that API functions are accessible and callable."""
        api_functions = [
            'create_container_registry_auth', 'create_endpoint', 'create_pod', 'create_template',
            'delete_container_registry_auth', 'get_endpoints', 'get_gpu', 'get_gpus',
            'get_pod', 'get_pods', 'get_user', 'resume_pod', 'stop_pod', 'terminate_pod',
            'update_container_registry_auth', 'update_endpoint_template', 'update_user_settings'
        ]
        
        for func_name in api_functions:
            assert func_name in runpod.__all__
            assert hasattr(runpod, func_name)
            assert callable(getattr(runpod, func_name))

    def test_config_functions_accessible(self):
        """Test that config functions are accessible and callable."""
        config_functions = ['check_credentials', 'get_credentials', 'set_credentials']
        
        for func_name in config_functions:
            assert func_name in runpod.__all__
            assert hasattr(runpod, func_name)
            assert callable(getattr(runpod, func_name))

    def test_endpoint_classes_accessible(self):
        """Test that endpoint classes are accessible."""
        endpoint_classes = ['AsyncioEndpoint', 'AsyncioJob', 'Endpoint']
        
        for class_name in endpoint_classes:
            assert class_name in runpod.__all__
            assert hasattr(runpod, class_name)
            assert inspect.isclass(getattr(runpod, class_name))

    def test_serverless_module_accessible(self):
        """Test that serverless module is accessible."""
        assert 'serverless' in runpod.__all__
        assert hasattr(runpod, 'serverless')
        assert inspect.ismodule(runpod.serverless)

    def test_logger_class_accessible(self):
        """Test that RunPodLogger class is accessible."""
        assert 'RunPodLogger' in runpod.__all__
        assert hasattr(runpod, 'RunPodLogger')
        assert inspect.isclass(runpod.RunPodLogger)

    def test_version_accessible(self):
        """Test that __version__ is accessible."""
        assert '__version__' in runpod.__all__
        assert hasattr(runpod, '__version__')
        assert isinstance(runpod.__version__, str)

    def test_module_variables_accessible(self):
        """Test that module variables are accessible."""
        module_vars = ['SSH_KEY_PATH', 'profile', 'api_key', 'endpoint_url_base']
        
        for var_name in module_vars:
            assert var_name in runpod.__all__
            assert hasattr(runpod, var_name)

    def test_private_imports_not_exported(self):
        """Test that private imports are not in __all__."""
        private_symbols = {
            'logging', 'os', '_credentials'
        }
        all_symbols = set(runpod.__all__)
        
        for private_symbol in private_symbols:
            assert private_symbol not in all_symbols, f"Private symbol '{private_symbol}' should not be in __all__"

    def test_all_covers_expected_public_api(self):
        """Test that __all__ contains the expected public API symbols."""
        expected_symbols = {
            # API functions  
            'create_container_registry_auth', 'create_endpoint', 'create_pod', 'create_template',
            'delete_container_registry_auth', 'get_endpoints', 'get_gpu', 'get_gpus',
            'get_pod', 'get_pods', 'get_user', 'resume_pod', 'stop_pod', 'terminate_pod',
            'update_container_registry_auth', 'update_endpoint_template', 'update_user_settings',
            # Config functions
            'check_credentials', 'get_credentials', 'set_credentials',
            # Endpoint classes
            'AsyncioEndpoint', 'AsyncioJob', 'Endpoint',
            # Serverless module
            'serverless',
            # Logger class
            'RunPodLogger',
            # Version
            '__version__',
            # Module variables
            'SSH_KEY_PATH', 'profile', 'api_key', 'endpoint_url_base'
        }
        
        actual_symbols = set(runpod.__all__)
        assert expected_symbols == actual_symbols, f"Expected {expected_symbols}, got {actual_symbols}"

    def test_no_duplicate_symbols_in_all(self):
        """Test that __all__ contains no duplicate symbols."""
        all_symbols = runpod.__all__
        unique_symbols = set(all_symbols)
        assert len(all_symbols) == len(unique_symbols), f"Duplicates found in __all__: {[x for x in all_symbols if all_symbols.count(x) > 1]}"