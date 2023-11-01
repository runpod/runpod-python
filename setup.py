'''
runpod-python | setup.py
Called to setup the runpod-python package.
'''

from setuptools import setup, find_packages

# README.md > long_description
with open('README.md', encoding='utf-8') as long_description_file:
    long_description = long_description_file.read()

# requirements.txt > requirements
with open('requirements.txt', encoding="UTF-8") as requirements_file:
    install_requires = requirements_file.read().splitlines()

extras_require = {
    'test': [
        'asynctest',
        'nest_asyncio',
        'pylint',
        'pytest',
        'pytest-cov',
        'pytest-timeout',
        'pytest-asyncio',
    ]
}

if __name__ == "__main__":

    setup(
        name = 'runpod',

        use_scm_version = True,

        setup_requires = [
            'setuptools>=45',
            'setuptools_scm',
            'wheel'
        ],

        install_requires = install_requires,

        extras_require = extras_require,

        packages = find_packages(),

        python_requires = '>=3.8',

        description = '🐍 | Python library for RunPod API and serverless worker SDK.',

        long_description = long_description,

        long_description_content_type = 'text/markdown',

        author = 'RunPod',

        author_email = 'engineer@runpod.io',

        url = 'https://runpod.io',

        project_urls = {
            'Documentation': 'https://docs.runpod.io',
            'Source': 'https://github.com/runpod/runpod-python',
            'Bug Tracker': 'https://github.com/runpod/runpod-python/issues',
            'Changelog': 'https://github.com/runpod/runpod-python/blob/main/CHANGELOG.md'
        },

        classifiers = [
            'Environment :: Web Environment',
            'Intended Audience :: Developers',
            'License :: OSI Approved :: MIT License',
            'Operating System :: OS Independent',
            'Programming Language :: Python',
            'Programming Language :: Python :: 3',
            'Topic :: Internet :: WWW/HTTP',
            'Topic :: Internet :: WWW/HTTP :: Dynamic Content'
        ],

        include_package_data = True,

        entry_points = {
            'console_scripts': [
                'runpod = runpod.cli.entry:runpod_cli'
            ]
        },

        keywords = ['runpod', 'ai', 'gpu', 'serverless', 'SDK', 'API', 'python', 'library'],

        license = 'MIT'
    )
