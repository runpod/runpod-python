""" Example of using the RunPodLogger class. """""

import runpod

JOB_ID = '1234567890'
log = runpod.RunPodLogger()


log.debug('A debug message')
log.info('An info message')
log.warn('A warning message')
log.error('An error message')

# Output:
# DEBUG   | A debug message
# INFO    | An info message
# WARN    | A warning message
# ERROR   | An error message


log.debug('A debug message', request_id=JOB_ID)
log.info('An info message', request_id=JOB_ID)
log.warn('A warning message', request_id=JOB_ID)
log.error('An error message', request_id=JOB_ID)

# Output:
# {"requestId": "1234567890", "message": "A debug message", "level": "DEBUG"}
# {"requestId": "1234567890", "message": "An info message", "level": "INFO"}
# {"requestId": "1234567890", "message": "A warning message", "level": "WARN"}
# {"requestId": "1234567890", "message": "An error message", "level": "ERROR"}
