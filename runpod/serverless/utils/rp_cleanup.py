'''
runpod | serverless | cleanup.py

Called to clean up the worker pod after a job is completed.
'''

import os
import shutil


def clean(folder_list=None):
    '''
    Removes the downloads folder.
    '''
    shutil.rmtree("input_objects", ignore_errors=True)
    shutil.rmtree("output_objects", ignore_errors=True)

    if os.path.exists('output.zip'):
        os.remove('output.zip')

    if folder_list is not None:
        for folder in folder_list:
            shutil.rmtree(folder, ignore_errors=True)
