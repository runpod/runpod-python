""" Reads the .runpodignore file and returns a list of files to ignore. """

import os
import fnmatch

EXCLUDE_PATTERNS = [
        "__pycache__/",
        "*.pyc",
        ".*.swp",
        ".git/",
        "*.tmp",
        "*.log",
    ]


def get_ignore_list():
    """ Reads the .runpodignore file and returns a list of files to ignore. """
    ignore_list = EXCLUDE_PATTERNS.copy()
    ignore_file = os.path.join(os.getcwd(), '.runpodignore')

    if not os.path.isfile(ignore_file):
        return ignore_list

    with open(ignore_file, 'r', encoding="UTF-8") as ignore_file_handle:
        for line in ignore_file_handle:
            stripped_line = line.strip()
            if stripped_line and not stripped_line.startswith('#'):
                ignore_list.append(stripped_line)

    return ignore_list


def should_ignore(file_path, ignore_list=None):
    """ Returns True if the file should be ignored, False otherwise. """
    if ignore_list is None:
        ignore_list = get_ignore_list()

    relative_path = os.path.relpath(file_path, os.getcwd())

    for pattern in ignore_list:
        if pattern.startswith('/'):
            pattern = pattern[1:]

        if pattern.endswith('/'):
            pattern += '*'

        if fnmatch.fnmatch(relative_path, pattern):
            return True

    return False
