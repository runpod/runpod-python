''' PodWorker | modules | logging.py '''

import os
from dotenv import load_dotenv


env_path = os.getcwd() + '/.env'
load_dotenv(env_path)  # Load environment variables


def log(message, level='INFO'):
    '''
    Log message to stdout
    '''
    if os.environ.get('RUNPOD_DEBUG', 'False') == 'true':
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


log('Logging module loaded')

log_secret('RUNPOD_AI_API_KEY', os.environ.get('RUNPOD_AI_API_KEY', None))
log_secret('RUNPOD_WEBHOOK_GET_JOB', os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None))
log_secret('RUNPOD_WEBHOOK_POST_OUTPUT', os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT', None))

log_secret('RUNPOD_BUCKET_ENDPOINT_URL', os.environ.get('RUNPOD_BUCKET_ENDPOINT_URL', None))
log_secret('RUNPOD_BUCKET_ACCESS_KEY_ID', os.environ.get('RUNPOD_BUCKET_ACCESS_KEY_ID', None))
log_secret(
    'RUNPOD_BUCKET_SECRET_ACCESS_KEY',
    os.environ.get('RUNPOD_BUCKET_SECRET_ACCESS_KEY', None)
)
