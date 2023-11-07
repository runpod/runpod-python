""" Example of using the RunPodLogger class. """""

import runpod

log = runpod.RunPodLogger()

log.debug('A debug message')
log.info('An info message')
log.warn('A warning message')
log.error('An error message')

JOB_ID = '1234567890'

log.debug('A debug message', job_id=JOB_ID)
log.info('An info message', job_id=JOB_ID)
log.warn('A warning message', job_id=JOB_ID)
log.error('An error message', job_id=JOB_ID)
