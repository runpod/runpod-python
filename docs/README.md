# RunPod Python SDK Documentation Guide

This directory contains the Sphinx documentation for the RunPod Python SDK. This guide will help you set up, build, and contribute to the documentation.

## Prerequisites

Before you begin, ensure you have Python 3.7+ installed. You'll also need to install the documentation dependencies:

```bash
pip install -r requirements.txt
```

## Building the Documentation

### Quick Start

To build the documentation:

1. Navigate to the docs directory:
   ```bash
   cd docs
   ```

2. Build the HTML documentation:
   ```bash
   make html
   ```

The built documentation will be available in `build/html/`. Open `build/html/index.html` in your web browser to view it.

### Other Build Options

- Clean and rebuild:
  ```bash
  make clean html
  ```

- Build specific formats:
  ```bash
  make latexpdf  # Build PDF documentation
  make epub      # Build EPUB documentation
  ```

## Documentation Structure

```
docs/
├── Makefile            # Build system
├── requirements.txt    # Documentation dependencies
├── source/
│   ├── conf.py        # Sphinx configuration
│   ├── index.rst      # Documentation homepage
│   ├── installation.rst    # Installation guide
│   ├── quickstart.rst     # Getting started guide
│   ├── api/               # API reference
│   │   ├── index.rst     # API overview
│   │   ├── error.rst     # Error handling
│   │   ├── http_client.rst   # HTTP client
│   │   └── serverless/   # Serverless components
│   │       ├── core.rst      # Core functionality
│   │       ├── worker.rst    # Worker implementation
│   │       └── modules/      # Additional modules
│   └── _static/         # Static files (images, custom CSS)
└── build/              # Built documentation
    └── html/           # HTML output
```

## Common Issues and Solutions

### Missing API Key Warning
If you see warnings about missing API keys during the build:
```
ValueError: No authentication credentials found. Please set RUNPOD_AI_API_KEY
```
This is expected in a development environment and won't affect the documentation quality. The API key is only required for running the actual SDK.

### Import Warnings
If you encounter import-related warnings, ensure you have:
1. Installed the package in development mode: `pip install -e ..`
2. Installed all required dependencies: `pip install -r requirements.txt`

### Title Underline Warnings
Warnings about title underlines being too short are formatting issues. Ensure your RST files use consistent title decoration:
```rst
Section Title
============

Subsection Title
---------------

Sub-subsection Title
~~~~~~~~~~~~~~~~~~~
```

## Contributing to Documentation

### Adding New Pages
1. Create a new `.rst` file in the appropriate directory under `source/`
2. Add the file to the relevant `toctree` directive in `index.rst` or parent page
3. Use proper RST syntax for headings, code blocks, and cross-references

### Style Guidelines
1. Use Google-style docstrings in Python code
2. Keep line length under 100 characters
3. Use proper RST directives for:
   - Code examples: `.. code-block:: python`
   - Notes and warnings: `.. note::`, `.. warning::`
   - Cross-references: `:ref:`, `:class:`, `:meth:`

### Building for Production
For production builds:
1. Clean previous builds: `make clean`
2. Run spell check: `make spelling`
3. Build HTML: `make html`
4. Check for broken links: `make linkcheck`

## Maintenance

### Regular Updates
1. Keep dependencies up to date in `requirements.txt`
2. Review and update code examples
3. Verify cross-references and links
4. Update version numbers in `conf.py`

### Version Control
1. Document significant changes in changelog
2. Tag documentation versions to match SDK releases
3. Keep main branch documentation in sync with latest stable release

## Additional Resources

- [Sphinx Documentation](https://www.sphinx-doc.org/)
- [reStructuredText Guide](https://www.sphinx-doc.org/en/master/usage/restructuredtext/basics.html)
- [RunPod API Reference](https://docs.runpod.io/reference)
