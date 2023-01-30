''' PodWorker | modules | logging.py '''

import os
from dotenv import load_dotenv

env_path = os.getcwd() + '/.env'
load_dotenv(env_path)  # Load environment variables


def log(message, level='INFO'):
    '''
    Log message to stdout if RUNPOD_DEBUG is true.
    '''
    set_level = os.environ.get('RUNPOD_DEBUG_LEVEL', 'DEBUG').upper()
    level = level.ljust(7)

    if os.environ.get('RUNPOD_DEBUG', 'true') != 'true':
        return

    if set_level == 'ERROR' and level != 'ERROR':
        return

    if set_level == 'WARN' and level not in ['ERROR', 'WARN']:
        return

    if set_level == 'INFO' and level not in ['ERROR', 'WARN', 'INFO']:
        return

    print(f'{level}| {message}', flush=True)
    return


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
    log(message, 'ERROR')


def warn(message):
    '''
    warn log
    '''
    log(message, 'WARN')


def info(message):
    '''
    info log
    '''
    log(message, 'INFO')


def debug(message):
    '''
    debug log
    '''
    log(message, 'DEBUG')


log('Logging module loaded')

log_secret('RUNPOD_AI_API_KEY', os.environ.get('RUNPOD_AI_API_KEY', None))
log_secret('RUNPOD_WEBHOOK_GET_JOB', os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None))
log_secret('RUNPOD_WEBHOOK_POST_OUTPUT', os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT', None))
