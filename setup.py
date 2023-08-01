'''
runpod-python | setup.py
Called to setup the runpod-python package.
'''

from setuptools import setup

if __name__ == "__main__":
    setup(
        name='runpod',
        use_scm_version=True,
        setup_requires=['setuptools_scm'],

        entry_points={
            'console_scripts': [
                'runpod = runpod.cli:main'
            ]
        }
    )
