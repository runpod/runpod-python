""" Reads the .runpodignore file and returns a list of files to ignore. """

import os

EXCLUDE_PATTERNS = [
        "__pycache__/",
        "*.pyc",
        ".*.swp",
        ".git/"
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
