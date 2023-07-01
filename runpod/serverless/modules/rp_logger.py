'''
PodWorker | modules | logging.py

Debug Levels (Level - Value - Description)

NOTSET - 0 - No logging is configured, the logging system is effectively disabled.
DEBUG - 1 - Detailed information, typically of interest only when diagnosing problems. (Default)
INFO - 2 - Confirmation that things are working as expected.
WARN - 3 - An indication that something unexpected happened.
ERROR - 4 - Serious problem, the software has not been able to perform some function.
'''

import os
from dotenv import load_dotenv

env_path = os.path.join(os.getcwd(), '.env')
load_dotenv(env_path)  # Load environment variables

DEBUG_LEVELS = ['NOTSET', 'DEBUG', 'INFO', 'WARN', 'ERROR']


def _validate_debug_level(debug_level):
    '''
    Checks the debug level and returns the debug level name.
    '''
    if isinstance(debug_level, str):
        debug_level = debug_level.upper()

        if debug_level not in DEBUG_LEVELS:
            raise ValueError(f'Invalid debug level: {debug_level}')

        return debug_level

    if isinstance(debug_level, int):
        if debug_level < 0 or debug_level > 4:
            raise ValueError(f'Invalid debug level: {debug_level}')

        return DEBUG_LEVELS[debug_level]

    raise ValueError(f'Invalid debug level: {debug_level}')


class RunPodLogger:
    '''Singleton class for logging.'''

    __instance = None
    debug_level = _validate_debug_level(os.environ.get('RUNPOD_DEBUG_LEVEL', 'DEBUG'))

    def __new__(cls):
        if RunPodLogger.__instance is None:
            RunPodLogger.__instance = object.__new__(cls)
        return RunPodLogger.__instance

    def set_level(self, new_level):
        '''
        Set the debug level for logging.
        Can be set to the name or value of the debug level.
        '''
        self.debug_level = _validate_debug_level(new_level)

    def log(self, message, message_level='INFO'):
        '''
        Log message to stdout if RUNPOD_DEBUG is true.
        '''
        if DEBUG_LEVELS[self.debug_level] == 'NOTSET':
            return

        if self.debug_level > DEBUG_LEVELS.index(message_level) and message_level != 'TIP':
            return

        print(f'{message_level.ljust(7)}| {message}', flush=True)
        return

    def secret(self, secret_name, secret):
        '''
        Censors secrets for logging.
        Replaces everything except the first and last characters with *
        '''
        secret = str(secret)
        redacted_secret = secret[0] + '*' * (len(secret)-2) + secret[-1]
        self.info(f"{secret_name}: {redacted_secret}")

    def debug(self, message):
        '''
        debug log
        '''
        self.log(message, 'DEBUG')

    def info(self, message):
        '''
        info log
        '''
        self.log(message, 'INFO')

    def warn(self, message):
        '''
        warn log
        '''
        self.log(message, 'WARN')

    def error(self, message):
        '''
        error log
        '''
        self.log(message, 'ERROR')

    def tip(self, message):
        '''
        tip log
        '''
        self.log(message, 'TIP')
