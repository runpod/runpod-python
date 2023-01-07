''' PodWorker | modules | logging.py '''

import os
from dotenv import load_dotenv

LOG_LEVEL_ERROR = 'ERROR'
LOG_LEVEL_WARN = 'WARNING'
LOG_LEVEL_INFO = 'INFO'
LOG_LEVEL_DEBUG = 'DEBUG'

env_path = os.getcwd() + '/.env'
load_dotenv(env_path)  # Load environment variables


def log(message, level='INFO'):
    '''
    Log message to stdout if RUNPOD_DEBUG is true.
    '''
    if os.environ.get('RUNPOD_DEBUG', 'true') == 'true':
        print(f'{level} | {message}')


def log_secret(secret_name, secret, level='INFO'):
    '''
    Censors secrets for logging
    Replaces everything except the first and last characters with *
    '''
    if secret is None:
        secret = 'Could not read environment variable.'
        log(f"{secret_name}: {secret}", 'ERROR')
    else:
        secret = str(secret)
        redacted_secret = secret[0] + '*' * len(secret) + secret[-1]
        log(f"{secret_name}: {redacted_secret}", level)


def error(message):
    '''
    error log
    '''
    log(message, LOG_LEVEL_ERROR)


def warn(message):
    '''
    warn log
    '''
    log(message, LOG_LEVEL_WARN)


def info(message):
    '''
    info log
    '''
    log(message, LOG_LEVEL_INFO)


def debug(message):
    '''
    debug log
    '''
    log(message, LOG_LEVEL_DEBUG)


log('Logging module loaded')

log_secret('RUNPOD_AI_API_KEY', os.environ.get('RUNPOD_AI_API_KEY', None))
log_secret('RUNPOD_WEBHOOK_GET_JOB', os.environ.get(
    'RUNPOD_WEBHOOK_GET_JOB', None))
log_secret('RUNPOD_WEBHOOK_POST_OUTPUT', os.environ.get(
    'RUNPOD_WEBHOOK_POST_OUTPUT', None))
