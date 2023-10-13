'''
runpod-python | setup.py
Called to setup the runpod-python package.
'''

from setuptools import setup

with open('requirements.txt', encoding="UTF-8") as requirements_file:
    requirements = requirements_file.read().splitlines()

if __name__ == "__main__":
    setup(
        name='runpod',
        use_scm_version=True,
        setup_requires=['setuptools_scm'],
        install_requires=requirements,

        entry_points={
            'console_scripts': [
                'runpod = runpod.cli.commands:runpod_cli'
            ]
        }
    )
